import hashlib
from datetime import datetime, timezone
from typing import Any

from fastapi import Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.models import AudioObject, Call, Job


def compute_param_hash(
    language: str | None,
    channel_manager: Any,
    vocabulary_hints: Any,
) -> str:
    """Computes a stable 8-char hash of job processing options for content-based idempotency."""
    fingerprint = f"{language or 'ru'}:{channel_manager}:{vocabulary_hints}"
    return hashlib.sha256(fingerprint.encode()).hexdigest()[:8]


def parse_call_created_at(raw_value: Any) -> datetime | None:
    """Parses ISO-8601 timestamp into naive UTC datetime for Call.call_created_at."""
    if not raw_value:
        return None
    try:
        dt = datetime.fromisoformat(str(raw_value).replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        return None


def format_utc_datetime(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


async def find_existing_job(
    db: AsyncSession,
    namespace: str,
    idempotency_key: str,
) -> Job | None:
    stmt = select(Job).where(Job.namespace == namespace, Job.idempotency_key == idempotency_key)
    return (await db.execute(stmt)).scalar_one_or_none()


def check_idempotency_conflict(
    existing_job: Job,
    explicit_idempotency_key: str | None,
    file_hash: str | None,
) -> None:
    if (
        explicit_idempotency_key
        and file_hash
        and existing_job.file_hash_sha256
        and existing_job.file_hash_sha256 != file_hash
    ):
        raise AppError(
            error="idempotency_conflict",
            message="Idempotency key has already been used with different content",
            status_code=409,
        )


async def commit_job_records(
    db: AsyncSession,
    *,
    call: Call,
    job: Job,
    namespace: str,
    idempotency_key: str,
    response: Response,
    audio_obj: AudioObject | None = None,
    explicit_idempotency_key: str | None = None,
    file_hash: str | None = None,
) -> Job | None:
    """Commits Call, optional AudioObject, and Job with race-safe idempotency recovery.

    Returns the existing Job (and sets HTTP 200 on `response`) if a concurrent request
    committed the same idempotency key first; returns None when the new Job was committed.
    """
    db.add(call)
    if audio_obj is not None:
        db.add(audio_obj)
    db.add(job)

    try:
        await db.commit()
        return None
    except IntegrityError:
        await db.rollback()
        existing_job = await find_existing_job(db, namespace, idempotency_key)
        if existing_job:
            check_idempotency_conflict(existing_job, explicit_idempotency_key, file_hash)
            response.status_code = status.HTTP_200_OK
            return existing_job
        raise
