import hashlib
import uuid
from datetime import datetime
from typing import Any

from fastapi import (
    APIRouter,
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
from sqlalchemy.ext.asyncio import AsyncSession

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
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_request_id: str | None = Header(None, alias="X-Request-Id"),
    auth: AuthContext = Depends(verify_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require_scope("write")
    settings = get_settings()

    # Read uploaded bytes
    file_bytes = await file.read()
    if not file_bytes:
        raise AppError("empty_file", "Загруженный файл пуст", 400)

    # Size validation
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if len(file_bytes) > max_bytes:
        raise AppError("payload_too_large", f"Размер файла превышает лимит {settings.max_upload_size_mb} МБ", 413)

    # Calculate SHA-256 for file deduplication
    file_hash = hashlib.sha256(file_bytes).hexdigest()
    effective_idempotency = idempotency_key or f"hash:{file_hash}"

    # Check for existing job with same idempotency key
    stmt = select(Job).where(Job.namespace == namespace, Job.idempotency_key == effective_idempotency)
    existing_job = (await db.execute(stmt)).scalar_one_or_none()
    if existing_job:
        response.status_code = status.HTTP_200_OK
        return JobAcceptedResponse(
            job_id=existing_job.id,
            status=existing_job.status,
            status_url=f"/v1/jobs/{existing_job.id}",
            created_at=existing_job.created_at,
        )

    # Generate IDs
    job_id = str(uuid.uuid4())
    call_id = str(uuid.uuid4())
    now = datetime.utcnow()

    # Upload raw audio to MinIO
    storage = get_storage()
    s3_key = f"{namespace}/{now.strftime('%Y/%m/%d')}/{call_id}/original_{file.filename}"
    storage.upload_bytes(
        bucket=settings.s3_bucket_audio,
        key=s3_key,
        data=file_bytes,
        content_type=file.content_type or "application/octet-stream",
    )

    # Create Call record
    call = Call(
        id=call_id,
        namespace=namespace,
        external_id=external_id,
        direction=direction,
        manager_id=manager_id,
        client_id=client_id,
        created_at=now,
    )
    db.add(call)

    # Create AudioObject record
    audio_obj = AudioObject(
        call_id=call_id,
        namespace=namespace,
        s3_bucket=settings.s3_bucket_audio,
        s3_key=s3_key,
        mime_type=file.content_type,
        file_size_bytes=len(file_bytes),
        created_at=now,
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
    await db.commit()

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
    }
    await queue.enqueue_long_job(job_id, payload)

    return JobAcceptedResponse(
        job_id=job_id,
        status="queued",
        status_url=f"/v1/jobs/{job_id}",
        created_at=now,
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
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_request_id: str | None = Header(None, alias="X-Request-Id"),
    auth: AuthContext = Depends(verify_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require_scope("write")

    # SSRF Protection
    safe_url = validate_ssrf_url(req.url)

    effective_idempotency = idempotency_key or f"url:{safe_url}"
    stmt = select(Job).where(Job.namespace == req.namespace, Job.idempotency_key == effective_idempotency)
    existing_job = (await db.execute(stmt)).scalar_one_or_none()
    if existing_job:
        response.status_code = status.HTTP_200_OK
        return JobAcceptedResponse(
            job_id=existing_job.id,
            status=existing_job.status,
            status_url=f"/v1/jobs/{existing_job.id}",
            created_at=existing_job.created_at,
        )

    job_id = str(uuid.uuid4())
    call_id = str(uuid.uuid4())
    now = datetime.utcnow()
    meta = req.metadata or {}

    call = Call(
        id=call_id,
        namespace=req.namespace,
        external_id=meta.get("external_id"),
        direction=meta.get("direction", "inbound"),
        manager_id=meta.get("manager_id"),
        client_id=meta.get("client_id"),
        created_at=now,
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
    await db.commit()

    queue = get_queue()
    payload = {
        "job_id": job_id,
        "call_id": call_id,
        "namespace": req.namespace,
        "source_url": safe_url,
        "callback_url": req.callback_url,
        "metadata": meta,
        "options": req.options or {},
    }
    await queue.enqueue_long_job(job_id, payload)

    return JobAcceptedResponse(
        job_id=job_id,
        status="queued",
        status_url=f"/v1/jobs/{job_id}",
        created_at=now,
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
    db: AsyncSession = Depends(get_db),
):
    auth.require_scope("read")

    stmt = select(Job).where(Job.id == job_id, Job.namespace == namespace)
    job = (await db.execute(stmt)).scalar_one_or_none()

    if not job:
        raise AppError("job_not_found", f"Задание {job_id} не найдено в пространстве {namespace}", 404)

    result_url = f"/v1/calls/{job.call_id}/analysis" if job.status == "succeeded" and job.call_id else None

    return JobStatusResponse(
        job_id=job.id,
        call_id=job.call_id,
        status=job.status,
        progress=job.progress,
        step=job.step,
        result_url=result_url,
        error=job.error_details,
        created_at=job.created_at,
        updated_at=job.updated_at or job.created_at,
    )
