import hashlib
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Header,
    Query,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.audio_ingest import ingest_url_job
from app.api.rate_limit import rate_limit
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.security import AuthContext, validate_ssrf_url, verify_auth
from app.db.models import AudioObject, Call, Job
from app.db.session import get_db
from app.queue.redis_streams import get_queue
from app.storage.s3 import get_storage

router = APIRouter(prefix="/v1/jobs", tags=["jobs"])
logger = get_logger("jobs_api")


class JobAcceptedResponse(BaseModel):
    job_id: str
    status: str
    status_url: str
    created_at: datetime


class JobStatusResponse(BaseModel):
    job_id: str
    call_id: str | None = None
    status: str
    progress: int
    step: str
    result_url: str | None = None
    error: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class CreateJobByUrlRequest(BaseModel):
    namespace: str
    url: str
    callback_url: str | None = None
    language: str | None = "ru"
    metadata: dict[str, Any] | None = None
    options: dict[str, Any] | None = None


class CreateJobByS3Request(BaseModel):
    namespace: str
    s3_key: str
    s3_bucket: str | None = None
    callback_url: str | None = None
    language: str | None = "ru"
    metadata: dict[str, Any] | None = None
    options: dict[str, Any] | None = None


@router.post(
    "/upload",
    response_model=JobAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Создание задания через загрузку файла",
)
async def create_job_upload(
    response: Response,
    namespace: str = Form(...),
    file: UploadFile = File(...),
    callback_url: str | None = Form(None),
    external_id: str | None = Form(None),
    direction: str | None = Form("inbound"),
    manager_id: str | None = Form(None),
    client_id: str | None = Form(None),
    channel_manager: int | None = Form(None),
    vocabulary_hints: str | None = Form(None),
    language: str | None = Form("ru"),
    call_created_at: datetime | None = Form(None),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_request_id: str | None = Header(None, alias="X-Request-Id"),
    auth: AuthContext = Depends(verify_auth),
    _=Depends(rate_limit),
    db: AsyncSession = Depends(get_db),
):
    auth.require_scope("write")
    auth.verify_namespace(namespace)
    settings = get_settings()

    if callback_url:
        validate_ssrf_url(callback_url)

    # Stream uploaded audio directly to a temporary file to prevent RAM duplication and OOM
    tmp_upload = tempfile.NamedTemporaryFile(delete=False, suffix=".tmp")
    tmp_upload_path = tmp_upload.name
    tmp_upload.close()
    total_bytes = 0
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    hasher = hashlib.sha256()

    try:
        with open(tmp_upload_path, "wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)  # 1MB chunk
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    raise AppError(
                        "payload_too_large",
                        f"Размер файла превышает лимит {settings.max_upload_size_mb} МБ",
                        413,
                    )
                hasher.update(chunk)
                f.write(chunk)

        if total_bytes == 0:
            raise AppError("empty_file", "Загруженный файл пуст", 400)

        file_hash = hasher.hexdigest()
        param_fingerprint = f"{language or 'ru'}:{channel_manager}:{vocabulary_hints}"
        param_hash = hashlib.sha256(param_fingerprint.encode()).hexdigest()[:8]
        effective_idempotency = idempotency_key or f"hash:{file_hash}:{param_hash}"

        # Check for existing job with same idempotency key (explicit or content-hash derived)
        # Note on Idempotency semantics:
        # Strict and immutable: retries always return the exact existing job (including failed) with HTTP 200.
        stmt = select(Job).where(
            Job.namespace == namespace, Job.idempotency_key == effective_idempotency
        )
        existing_job = (await db.execute(stmt)).scalar_one_or_none()
        if existing_job:
            if (
                idempotency_key
                and existing_job.file_hash_sha256
                and existing_job.file_hash_sha256 != file_hash
            ):
                raise AppError(
                    error="idempotency_conflict",
                    message="Idempotency key has already been used with different content",
                    status_code=409,
                )
            response.status_code = status.HTTP_200_OK
            return JobAcceptedResponse(
                job_id=existing_job.id,
                status=existing_job.status,
                status_url=f"/v1/jobs/{existing_job.id}?namespace={namespace}",
                created_at=existing_job.created_at.replace(tzinfo=timezone.utc)
                if existing_job.created_at.tzinfo is None
                else existing_job.created_at,
            )

        # Generate IDs
        job_id = str(uuid.uuid4())
        call_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        # Upload raw audio to MinIO directly from disk
        storage = get_storage()
        s3_key = f"{namespace}/{now.strftime('%Y/%m/%d')}/{call_id}/original_{file.filename}"
        if hasattr(storage, "upload_file"):
            storage.upload_file(
                local_path=tmp_upload_path,
                bucket=settings.s3_bucket_audio,
                key=s3_key,
                content_type=file.content_type or "application/octet-stream",
            )
        else:
            with open(tmp_upload_path, "rb") as f:
                storage.upload_bytes(
                    bucket=settings.s3_bucket_audio,
                    key=s3_key,
                    data=f.read(),
                    content_type=file.content_type or "application/octet-stream",
                )
    finally:
        if os.path.exists(tmp_upload_path):
            try:
                os.remove(tmp_upload_path)
            except Exception:
                pass

    # Create Call record
    call = Call(
        id=call_id,
        namespace=namespace,
        external_id=external_id,
        direction=direction,
        call_created_at=call_created_at or now,
        manager_id=manager_id,
        client_id=client_id,
        created_at=now,
        expires_at=now + timedelta(days=settings.retention_text_days),
    )
    db.add(call)

    # Create AudioObject record
    audio_obj = AudioObject(
        call_id=call_id,
        namespace=namespace,
        s3_bucket=settings.s3_bucket_audio,
        s3_key=s3_key,
        mime_type=file.content_type,
        file_size_bytes=total_bytes,
        created_at=now,
        expires_at=now + timedelta(days=settings.retention_audio_days),
    )
    db.add(audio_obj)

    # Create Job record
    job = Job(
        id=job_id,
        namespace=namespace,
        call_id=call_id,
        status="queued",
        progress=0,
        step="uploaded",
        idempotency_key=effective_idempotency,
        file_hash_sha256=file_hash,
        callback_url=callback_url,
        created_at=now,
    )
    db.add(job)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing_job = (await db.execute(stmt)).scalar_one_or_none()
        if existing_job:
            if (
                idempotency_key
                and existing_job.file_hash_sha256
                and existing_job.file_hash_sha256 != file_hash
            ):
                raise AppError(
                    error="idempotency_conflict",
                    message="Idempotency key has already been used with different content",
                    status_code=409,
                )
            response.status_code = status.HTTP_200_OK
            return JobAcceptedResponse(
                job_id=existing_job.id,
                status=existing_job.status,
                status_url=f"/v1/jobs/{existing_job.id}?namespace={namespace}",
                created_at=existing_job.created_at.replace(tzinfo=timezone.utc)
                if existing_job.created_at.tzinfo is None
                else existing_job.created_at,
            )
        raise

    # Enqueue task in Redis Streams
    queue = get_queue()
    payload = {
        "job_id": job_id,
        "call_id": call_id,
        "namespace": namespace,
        "s3_bucket": settings.s3_bucket_audio,
        "s3_key": s3_key,
        "callback_url": callback_url,
        "channel_manager": channel_manager,
        "vocabulary_hints": vocabulary_hints,
        "language": language or "ru",
    }
    await queue.enqueue_long_job(job_id, payload)

    return JobAcceptedResponse(
        job_id=job_id,
        status="queued",
        status_url=f"/v1/jobs/{job_id}?namespace={namespace}",
        created_at=now.replace(tzinfo=timezone.utc),
    )


@router.post(
    "/by-url",
    response_model=JobAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Создание задания по ссылке на аудио",
)
async def create_job_by_url(
    req: CreateJobByUrlRequest,
    response: Response,
    background: BackgroundTasks,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_request_id: str | None = Header(None, alias="X-Request-Id"),
    auth: AuthContext = Depends(verify_auth),
    _=Depends(rate_limit),
    db: AsyncSession = Depends(get_db),
):
    auth.require_scope("write")
    auth.verify_namespace(req.namespace)
    settings = get_settings()

    # SSRF Protection
    safe_url = validate_ssrf_url(req.url)
    if req.callback_url:
        validate_ssrf_url(req.callback_url)

    meta = req.metadata or {}
    options = req.options or {}
    param_fingerprint = f"{req.language or 'ru'}:{options.get('channel_manager') or meta.get('channel_manager')}:{options.get('vocabulary_hints') or meta.get('vocabulary_hints')}"
    param_hash = hashlib.sha256(param_fingerprint.encode()).hexdigest()[:8]
    effective_idempotency = idempotency_key or f"url:{safe_url}:{param_hash}"
    stmt = select(Job).where(
        Job.namespace == req.namespace, Job.idempotency_key == effective_idempotency
    )
    existing_job = (await db.execute(stmt)).scalar_one_or_none()
    if existing_job:
        response.status_code = status.HTTP_200_OK
        return JobAcceptedResponse(
            job_id=existing_job.id,
            status=existing_job.status,
            status_url=f"/v1/jobs/{existing_job.id}?namespace={req.namespace}",
            created_at=existing_job.created_at.replace(tzinfo=timezone.utc)
            if existing_job.created_at.tzinfo is None
            else existing_job.created_at,
        )

    job_id = str(uuid.uuid4())
    call_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    call_created_at_dt = None
    if meta.get("call_created_at"):
        try:
            call_created_at_dt = datetime.fromisoformat(
                str(meta["call_created_at"]).replace("Z", "+00:00")
            ).replace(tzinfo=None)
        except Exception:
            call_created_at_dt = None

    call = Call(
        id=call_id,
        namespace=req.namespace,
        external_id=meta.get("external_id"),
        direction=meta.get("direction", "inbound"),
        call_created_at=call_created_at_dt or now,
        manager_id=meta.get("manager_id"),
        client_id=meta.get("client_id"),
        created_at=now,
        expires_at=now + timedelta(days=settings.retention_text_days),
    )
    db.add(call)

    job = Job(
        id=job_id,
        namespace=req.namespace,
        call_id=call_id,
        status="queued",
        progress=0,
        step="pending_download",
        idempotency_key=effective_idempotency,
        callback_url=req.callback_url,
        created_at=now,
    )
    db.add(job)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing_job = (await db.execute(stmt)).scalar_one_or_none()
        if existing_job:
            response.status_code = status.HTTP_200_OK
            return JobAcceptedResponse(
                job_id=existing_job.id,
                status=existing_job.status,
                status_url=f"/v1/jobs/{existing_job.id}?namespace={req.namespace}",
                created_at=existing_job.created_at.replace(tzinfo=timezone.utc)
                if existing_job.created_at.tzinfo is None
                else existing_job.created_at,
            )
        raise

    # B9: скачивание — в фоне после немедленного 202 (файл может быть большим,
    # клиент не ждёт). Worker-long без egress получает готовый s3_key.
    background.add_task(
        ingest_url_job,
        job_id=job_id,
        call_id=call_id,
        namespace=req.namespace,
        url=safe_url,
        callback_url=req.callback_url,
        language=req.language or "ru",
        metadata=meta,
        options=options,
        channel_manager=options.get("channel_manager") or meta.get("channel_manager"),
        vocabulary_hints=options.get("vocabulary_hints") or meta.get("vocabulary_hints"),
        max_bytes=settings.max_upload_size_mb * 1024 * 1024,
        source="by-url",
    )

    return JobAcceptedResponse(
        job_id=job_id,
        status="queued",
        status_url=f"/v1/jobs/{job_id}?namespace={req.namespace}",
        created_at=now.replace(tzinfo=timezone.utc),
    )


@router.post(
    "/by-s3",
    response_model=JobAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Создание задания по объекту в S3/MinIO",
)
async def create_job_by_s3(
    req: CreateJobByS3Request,
    response: Response,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_request_id: str | None = Header(None, alias="X-Request-Id"),
    auth: AuthContext = Depends(verify_auth),
    _=Depends(rate_limit),
    db: AsyncSession = Depends(get_db),
):
    auth.require_scope("write")
    auth.verify_namespace(req.namespace)
    settings = get_settings()

    if req.callback_url:
        validate_ssrf_url(req.callback_url)

    bucket = req.s3_bucket or settings.s3_bucket_audio
    storage = get_storage()
    obj_meta = storage.head_object(bucket, req.s3_key)
    if not obj_meta:
        raise AppError(
            "s3_object_not_found",
            f"Аудиообъект '{req.s3_key}' не найден в корзине '{bucket}'",
            404,
        )

    file_size = obj_meta.get("ContentLength", 0)
    meta = req.metadata or {}
    options = req.options or {}
    param_fingerprint = (
        f"{req.language or 'ru'}:"
        f"{options.get('channel_manager') or meta.get('channel_manager')}:"
        f"{options.get('vocabulary_hints') or meta.get('vocabulary_hints')}"
    )
    param_hash = hashlib.sha256(param_fingerprint.encode()).hexdigest()[:8]
    effective_idempotency = idempotency_key or f"s3:{bucket}:{req.s3_key}:{param_hash}"

    stmt = select(Job).where(
        Job.namespace == req.namespace, Job.idempotency_key == effective_idempotency
    )
    existing_job = (await db.execute(stmt)).scalar_one_or_none()
    if existing_job:
        response.status_code = status.HTTP_200_OK
        return JobAcceptedResponse(
            job_id=existing_job.id,
            status=existing_job.status,
            status_url=f"/v1/jobs/{existing_job.id}?namespace={req.namespace}",
            created_at=existing_job.created_at.replace(tzinfo=timezone.utc)
            if existing_job.created_at.tzinfo is None
            else existing_job.created_at,
        )

    job_id = str(uuid.uuid4())
    call_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    call_created_at_dt = None
    if meta.get("call_created_at"):
        try:
            call_created_at_dt = datetime.fromisoformat(
                str(meta["call_created_at"]).replace("Z", "+00:00")
            ).replace(tzinfo=None)
        except Exception:
            call_created_at_dt = None

    call = Call(
        id=call_id,
        namespace=req.namespace,
        external_id=meta.get("external_id"),
        direction=meta.get("direction", "inbound"),
        call_created_at=call_created_at_dt or now,
        manager_id=meta.get("manager_id"),
        client_id=meta.get("client_id"),
        created_at=now,
        expires_at=now + timedelta(days=settings.retention_text_days),
    )
    db.add(call)

    audio_obj = AudioObject(
        call_id=call_id,
        namespace=req.namespace,
        s3_bucket=bucket,
        s3_key=req.s3_key,
        mime_type=obj_meta.get("ContentType", "audio/wav"),
        file_size_bytes=file_size,
        created_at=now,
        expires_at=now + timedelta(days=settings.retention_audio_days),
    )
    db.add(audio_obj)

    job = Job(
        id=job_id,
        namespace=req.namespace,
        call_id=call_id,
        status="queued",
        progress=0,
        step="queued_s3",
        idempotency_key=effective_idempotency,
        callback_url=req.callback_url,
        created_at=now,
    )
    db.add(job)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing_job = (await db.execute(stmt)).scalar_one_or_none()
        if existing_job:
            response.status_code = status.HTTP_200_OK
            return JobAcceptedResponse(
                job_id=existing_job.id,
                status=existing_job.status,
                status_url=f"/v1/jobs/{existing_job.id}?namespace={req.namespace}",
                created_at=existing_job.created_at.replace(tzinfo=timezone.utc)
                if existing_job.created_at.tzinfo is None
                else existing_job.created_at,
            )
        raise

    queue = get_queue()
    payload = {
        "job_id": job_id,
        "call_id": call_id,
        "namespace": req.namespace,
        "s3_bucket": bucket,
        "s3_key": req.s3_key,
        "callback_url": req.callback_url,
        "language": req.language or "ru",
        "metadata": meta,
        "options": options,
        "channel_manager": options.get("channel_manager") or meta.get("channel_manager"),
        "vocabulary_hints": options.get("vocabulary_hints") or meta.get("vocabulary_hints"),
    }
    await queue.enqueue_long_job(job_id, payload)

    return JobAcceptedResponse(
        job_id=job_id,
        status="queued",
        status_url=f"/v1/jobs/{job_id}?namespace={req.namespace}",
        created_at=now.replace(tzinfo=timezone.utc),
    )


@router.get(
    "/{job_id}",
    response_model=JobStatusResponse,
    summary="Получение статуса задания",
)
async def get_job(
    job_id: str,
    namespace: str = Query(...),
    auth: AuthContext = Depends(verify_auth),
    _=Depends(rate_limit),
    db: AsyncSession = Depends(get_db),
):
    auth.require_scope("read")
    auth.verify_namespace(namespace)

    stmt = select(Job).where(Job.id == job_id, Job.namespace == namespace)
    job = (await db.execute(stmt)).scalar_one_or_none()

    if not job:
        raise AppError(
            "job_not_found", f"Задание {job_id} не найдено в пространстве {namespace}", 404
        )

    result_url = (
        f"/v1/calls/{job.call_id}/analysis?namespace={namespace}"
        if job.status == "succeeded" and job.call_id
        else None
    )

    created_at_tz = (
        job.created_at.replace(tzinfo=timezone.utc)
        if job.created_at.tzinfo is None
        else job.created_at
    )
    updated_dt = job.updated_at or job.created_at
    updated_at_tz = (
        updated_dt.replace(tzinfo=timezone.utc) if updated_dt.tzinfo is None else updated_dt
    )

    return JobStatusResponse(
        job_id=job.id,
        call_id=job.call_id,
        status=job.status,
        progress=job.progress,
        step=job.step,
        result_url=result_url,
        error=job.error_details,
        created_at=created_at_tz,
        updated_at=updated_at_tz,
    )
