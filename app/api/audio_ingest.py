"""Скачивание аудио по внешнему URL в объектное хранилище (сторона api).

Воркеры распознавания живут в изолированной сети compose без выхода в интернет
(B9), поэтому скачивание по `source_url` выполняется здесь, в `api` (у него есть
egress), с той же SSRF-защитой (`download_file_ssrf_safe`). Воркер получает
готовый `s3_key` и egress ему не нужен.

Скачивание идёт в фоне (FastAPI BackgroundTasks) после немедленного ответа 202:
клиент не ждёт файл до 500 МБ. Прогресс виден через GET /v1/jobs/{id}
(`pending_download` -> `downloaded`, при обрыве — `failed` + callback).
"""

import os
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import update

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.security import download_file_ssrf_safe
from app.db.models import AudioObject, Job
from app.db.session import AsyncSessionLocal
from app.queue.redis_streams import get_queue
from app.storage.s3 import get_storage

logger = get_logger("audio_ingest")


async def fetch_url_to_s3(
    *,
    url: str,
    namespace: str,
    call_id: str,
    max_bytes: int,
) -> tuple[str, str, int]:
    """Скачивает файл по URL (SSRF-safe) и кладёт в бакет аудио.

    Возвращает (s3_key, sha256_hex, size_bytes). Ошибки скачивания пробрасываются
    как AppError (413/400/...), записей в БД до успешного скачивания не создаётся.
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    s3_key = f"{namespace}/{now.strftime('%Y/%m/%d')}/{call_id}/original_from_url"

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".tmp")
    tmp_path = tmp.name
    tmp.close()
    try:
        file_hash = await download_file_ssrf_safe(
            url=url,
            target_path=tmp_path,
            max_bytes=max_bytes,
            timeout=60.0,
        )
        size_bytes = os.path.getsize(tmp_path)
        storage = get_storage()
        if hasattr(storage, "upload_file"):
            storage.upload_file(
                local_path=tmp_path,
                bucket=settings.s3_bucket_audio,
                key=s3_key,
                content_type="application/octet-stream",
            )
        else:  # pragma: no cover — запасной путь для несовместимых стореджей
            with open(tmp_path, "rb") as f:
                storage.upload_bytes(
                    bucket=settings.s3_bucket_audio,
                    key=s3_key,
                    data=f.read(),
                    content_type="application/octet-stream",
                )
        return s3_key, file_hash, size_bytes
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


async def ingest_url_job(
    *,
    job_id: str,
    call_id: str,
    namespace: str,
    url: str,
    callback_url: str | None,
    language: str,
    metadata: dict[str, Any],
    options: dict[str, Any],
    channel_manager: int | None,
    vocabulary_hints: list[str] | str | None,
    max_bytes: int,
    source: str = "by-url",
) -> None:
    """Фоновая задача: скачать аудио по URL, положить в S3, поставить задание.

    Вызывается из BackgroundTasks после ответа 202, работает в собственной
    DB-сессии (сессия запроса к этому моменту закрыта). При обрыве скачивания
    задание помечается failed с error_details и уходит failure-callback —
    так же, как при падении воркера.
    """
    settings = get_settings()
    queue = get_queue()

    async def _fail(error_type: str, error_msg: str) -> None:
        logger.error("url_ingest_failed", job_id=job_id, error_type=error_type, error=error_msg)
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(
                    status="failed",
                    step=error_type,
                    error_details={"error": error_type, "message": error_msg},
                )
            )
            await db.commit()
        if callback_url:
            try:
                await queue.enqueue_callback(
                    job_id,
                    callback_url,
                    {
                        "job_id": job_id,
                        "call_id": call_id,
                        "status": "failed",
                        "error": {"error": error_type, "message": error_msg},
                    },
                )
            except Exception as cb_err:
                logger.error("url_ingest_failure_callback_error", job_id=job_id, error=str(cb_err))

    try:
        s3_key, file_hash, file_size = await fetch_url_to_s3(
            url=url,
            namespace=namespace,
            call_id=call_id,
            max_bytes=max_bytes,
        )
    except AppError as e:
        await _fail(e.error, e.message)
        return
    except Exception as e:
        await _fail("audio_download_error", str(e))
        return

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with AsyncSessionLocal() as db:
        db.add(
            AudioObject(
                call_id=call_id,
                namespace=namespace,
                s3_bucket=settings.s3_bucket_audio,
                s3_key=s3_key,
                mime_type="application/octet-stream",
                file_size_bytes=file_size,
                created_at=now,
                expires_at=now + timedelta(days=settings.retention_audio_days),
            )
        )
        await db.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                status="queued",
                progress=0,
                step="downloaded",
                file_hash_sha256=file_hash,
            )
        )
        await db.commit()

    payload: dict[str, Any] = {
        "job_id": job_id,
        "call_id": call_id,
        "namespace": namespace,
        "s3_bucket": settings.s3_bucket_audio,
        "s3_key": s3_key,
        "callback_url": callback_url,
        "language": language,
        "metadata": metadata,
        "options": options,
        "channel_manager": channel_manager,
        "vocabulary_hints": vocabulary_hints,
    }
    await queue.enqueue_long_job(job_id, payload)
    logger.info("url_ingest_enqueued", job_id=job_id, call_id=call_id, source=source)
