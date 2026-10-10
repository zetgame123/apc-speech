import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.audio_ingest import fetch_url_to_s3
from app.api.rate_limit import rate_limit
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.security import (
    AuthContext,
    validate_ssrf_url,
)
from app.db.models import AudioObject, Call, Job
from app.db.session import get_db
from app.integrations.bitrix24 import parse_bitrix24_webhook
from app.queue.redis_streams import get_queue

router = APIRouter(prefix="/v1/integrations", tags=["integrations"])
logger = get_logger("integrations_api")


class BitrixWebhookResponse(BaseModel):
    status: str
    job_id: str
    call_id: str
    status_url: str
    created_at: datetime


async def verify_integration_auth(
    request: Request,
    token: str | None = Query(None),
    auth_token: str | None = Query(None),
) -> AuthContext:
    """
    Authenticates integration webhooks.
    Supports either standard Authorization: Bearer <token> or query parameters (?token=... or ?auth_token=...).
    """
    provided_token = token or auth_token

    if not provided_token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            provided_token = auth_header.replace("Bearer ", "").strip()

    if not provided_token:
        raise AppError(
            error="unauthorized",
            message="Отсутствует токен авторизации вебхука",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    from app.core.security import authenticate_token

    return authenticate_token(provided_token)


@router.post(
    "/bitrix24/webhook",
    response_model=BitrixWebhookResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Вебхук телефонии Битрикс24 (ONVOXIMPLANTCALLFINISHED)",
)
async def bitrix24_telephony_webhook(
    request: Request,
    response: Response,
    namespace: str = Query("default", description="Namespace изоляции данных"),
    bitrix_rest_url: str | None = Query(
        None,
        description="Входящий вебхук Битрикс24 для сохранения расшифровки в таймлайн (https://<portal>/rest/<user>/<token>/)",
    ),
    language: str = Query("ru", description="Язык распознавания: ru, en или auto"),
    callback_url: str | None = Query(None, description="Внешний callback URL"),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    auth: AuthContext = Depends(verify_integration_auth),
    _=Depends(rate_limit),
    db: AsyncSession = Depends(get_db),
):
    """
    Receives call-finished event from Bitrix24 telephony, downloads the call recording,
    enqueues ASR transcription & analysis, and optionally attaches results back to the CRM deal/lead timeline.
    """
    auth.require_scope("write")
    auth.verify_namespace(namespace)
    settings = get_settings()

    if callback_url:
        validate_ssrf_url(callback_url)
    if bitrix_rest_url:
        validate_ssrf_url(bitrix_rest_url)

    # Parse incoming request body (handles JSON and form-urlencoded)
    content_type = request.headers.get("content-type", "")
    body_data: dict[str, Any] = {}
    if "application/json" in content_type:
        try:
            body_data = await request.json()
        except Exception:
            body_data = {}
    else:
        try:
            form = await request.form()
            body_data = dict(form)
        except Exception:
            body_data = {}

    parsed = parse_bitrix24_webhook(body_data)

    if not parsed["record_url"]:
        logger.warning("bitrix24_missing_record_url", raw_keys=list(body_data.keys()))
        raise AppError(
            error="missing_record_url",
            message="В вебхуке Битрикс24 отсутствует ссылка на аудиозапись (RECORD_URL)",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # Validate SSRF
    safe_url = validate_ssrf_url(parsed["record_url"])

    effective_idempotency = idempotency_key or f"bitrix24:{parsed['external_id'] or safe_url}"

    # Check for existing duplicate job
    stmt = select(Job).where(
        Job.namespace == namespace, Job.idempotency_key == effective_idempotency
    )
    existing_job = (await db.execute(stmt)).scalar_one_or_none()
    if existing_job:
        if existing_job.status == "failed":
            existing_job.idempotency_key = f"{effective_idempotency[:100]}:f:{existing_job.id[:8]}"
            await db.flush()
        else:
            response.status_code = status.HTTP_200_OK
            return BitrixWebhookResponse(
                status=existing_job.status,
                job_id=existing_job.id,
                call_id=existing_job.call_id or "",
                status_url=f"/v1/jobs/{existing_job.id}?namespace={namespace}",
                created_at=existing_job.created_at,
            )

    job_id = str(uuid.uuid4())
    call_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # B9: worker-long живёт в сети без egress, поэтому скачиваем запись здесь,
    # в api (у него есть egress), с той же SSRF-защитой. Ошибка скачивания —
    # сразу 4xx ответ, записей в БД не создаём.
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    s3_key, file_hash, file_size = await fetch_url_to_s3(
        url=safe_url,
        namespace=namespace,
        call_id=call_id,
        max_bytes=max_bytes,
    )

    call = Call(
        id=call_id,
        namespace=namespace,
        external_id=parsed["external_id"],
        direction=parsed["direction"],
        manager_id=parsed["manager_id"],
        client_id=parsed["client_id"],
        duration_sec=parsed["duration_sec"],
        created_at=now,
        expires_at=now + timedelta(days=settings.retention_text_days),
    )
    db.add(call)

    # Аудио уже скачано в S3 (см. выше): фиксируем объект, как в /upload.
    audio_obj = AudioObject(
        call_id=call_id,
        namespace=namespace,
        s3_bucket=settings.s3_bucket_audio,
        s3_key=s3_key,
        mime_type="application/octet-stream",
        file_size_bytes=file_size,
        created_at=now,
        expires_at=now + timedelta(days=settings.retention_audio_days),
    )
    db.add(audio_obj)

    job = Job(
        id=job_id,
        namespace=namespace,
        call_id=call_id,
        status="queued",
        progress=0,
        step="downloaded",
        idempotency_key=effective_idempotency,
        file_hash_sha256=file_hash,
        callback_url=callback_url,
        created_at=now,
    )
    db.add(job)
    await db.commit()

    # Determine timeline REST URL (query param or inferred from auth)
    timeline_webhook = bitrix_rest_url
    if not timeline_webhook and parsed.get("portal_domain") and parsed.get("auth_token"):
        timeline_webhook = f"https://{parsed['portal_domain']}/rest/{parsed.get('manager_id', '1')}/{parsed['auth_token']}/"

    # Enqueue in Redis
    queue = get_queue()
    payload = {
        "job_id": job_id,
        "call_id": call_id,
        "namespace": namespace,
        "s3_bucket": settings.s3_bucket_audio,
        "s3_key": s3_key,
        "callback_url": callback_url,
        "language": language,
        "metadata": {
            "source": "bitrix24",
            "external_id": parsed["external_id"],
            "manager_id": parsed["manager_id"],
            "client_id": parsed["client_id"],
            "direction": parsed["direction"],
            "bitrix24": {
                "crm_entity_type": parsed["crm_entity_type"],
                "crm_entity_id": parsed["crm_entity_id"],
                "crm_activity_id": parsed["crm_activity_id"],
                "portal_domain": parsed["portal_domain"],
                "webhook_url": timeline_webhook,
            },
        },
        "options": {
            "language": language,
        },
    }
    await queue.enqueue_long_job(job_id, payload)

    logger.info(
        "bitrix24_call_enqueued",
        job_id=job_id,
        call_id=call_id,
        external_id=parsed["external_id"],
        crm_entity=f"{parsed['crm_entity_type']}:{parsed['crm_entity_id']}",
    )

    return BitrixWebhookResponse(
        status="queued",
        job_id=job_id,
        call_id=call_id,
        status_url=f"/v1/jobs/{job_id}?namespace={namespace}",
        created_at=now,
    )
