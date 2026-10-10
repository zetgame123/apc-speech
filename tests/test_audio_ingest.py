"""Пункт 3: скачивание по URL идёт в фоне после быстрого 202.

- Хендлеры by-url/Bitrix не ждут скачивание (BackgroundTasks).
- ingest_url_job: успех -> AudioObject + job=downloaded + s3-payload воркеру;
  обрыв -> job=failed + failure-callback, без падения.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("DEV_TOKEN", "dev-token")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("S3_ENDPOINT_URL", "http://localhost:9000")
os.environ.setdefault("CALLBACK_SIGNING_SECRET", "test-secret-key-32-characters!!!")

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

import app.api.audio_ingest as ingest_mod
from app.api.audio_ingest import ingest_url_job, reap_stalled_downloads
from app.core.errors import AppError
from app.db.models import AudioObject, Call, Job
from tests.conftest import TestingSessionLocal


def _session_cm():
    """Контекстный менеджер вокруг реальной sqlite-сессии для ingest."""
    session = TestingSessionLocal()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


async def _make_pending_job(job_id="job-ingest-1", call_id="call-ingest-1"):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with TestingSessionLocal() as session:
        session.add(
            Call(
                id=call_id,
                namespace="test_msk",
                direction="inbound",
                call_created_at=now,
                created_at=now,
                expires_at=now,
            )
        )
        session.add(
            Job(
                id=job_id,
                namespace="test_msk",
                call_id=call_id,
                status="queued",
                progress=0,
                step="pending_download",
                idempotency_key="test-key",
                created_at=now,
            )
        )
        await session.commit()
    return job_id, call_id


def _ingest_kwargs(job_id, call_id, **over):
    kwargs = dict(
        job_id=job_id,
        call_id=call_id,
        namespace="test_msk",
        url="https://example.com/audio.wav",
        callback_url="https://crm.example.com/hook",
        language="ru",
        metadata={},
        options={},
        channel_manager=None,
        vocabulary_hints=None,
        max_bytes=500 * 1024 * 1024,
        source="by-url",
    )
    kwargs.update(over)
    return kwargs


@pytest.mark.asyncio
async def test_ingest_url_job_success_enqueues_s3_payload():
    job_id, call_id = await _make_pending_job()
    mock_queue = MagicMock()
    mock_queue.enqueue_long_job = AsyncMock(return_value="1-0")
    mock_queue.enqueue_callback = AsyncMock(return_value="2-0")

    with (
        patch.object(
            ingest_mod,
            "fetch_url_to_s3",
            AsyncMock(return_value=("test_msk/k/original_from_url", "filehash123", 12345)),
        ) as mock_fetch,
        patch.object(ingest_mod, "get_queue", return_value=mock_queue),
        patch.object(ingest_mod, "AsyncSessionLocal", return_value=_session_cm()),
    ):
        await ingest_url_job(**_ingest_kwargs(job_id, call_id))

    mock_fetch.assert_awaited_once()
    # Воркеру ушёл s3_key, а не source_url (у воркера нет egress, см. B9).
    mock_queue.enqueue_long_job.assert_awaited_once()
    payload = mock_queue.enqueue_long_job.await_args[0][1]
    assert payload["s3_key"] == "test_msk/k/original_from_url"
    assert "source_url" not in payload
    mock_queue.enqueue_callback.assert_not_awaited()

    async with TestingSessionLocal() as session:
        job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
        assert job.step == "downloaded"
        assert job.file_hash_sha256 == "filehash123"
        assert job.status == "queued"
        audio = (
            await session.execute(select(AudioObject).where(AudioObject.call_id == call_id))
        ).scalar_one()
        assert audio.s3_key == "test_msk/k/original_from_url"
        assert audio.file_size_bytes == 12345


@pytest.mark.asyncio
async def test_ingest_url_job_failure_marks_job_failed_and_callbacks():
    job_id, call_id = await _make_pending_job(job_id="job-ingest-2", call_id="call-ingest-2")
    mock_queue = MagicMock()
    mock_queue.enqueue_long_job = AsyncMock(return_value="1-0")
    mock_queue.enqueue_callback = AsyncMock(return_value="2-0")

    with (
        patch.object(
            ingest_mod,
            "fetch_url_to_s3",
            AsyncMock(
                side_effect=AppError(
                    error="payload_too_large",
                    message="Размер файла превышает лимит",
                    status_code=413,
                )
            ),
        ),
        patch.object(ingest_mod, "get_queue", return_value=mock_queue),
        patch.object(ingest_mod, "AsyncSessionLocal", return_value=_session_cm()),
    ):
        await ingest_url_job(**_ingest_kwargs(job_id, call_id))

    mock_queue.enqueue_long_job.assert_not_awaited()
    mock_queue.enqueue_callback.assert_awaited_once()
    failure = mock_queue.enqueue_callback.await_args[0][2]
    assert failure["status"] == "failed"
    assert failure["error"]["error"] == "payload_too_large"

    async with TestingSessionLocal() as session:
        job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
        assert job.status == "failed"
        assert job.error_details["error"] == "payload_too_large"


@pytest.mark.asyncio
async def test_reap_stalled_downloads_marks_old_pending_failed():
    """Уборка: зависший pending_download -> failed + callback; свежие не трогаем."""
    from datetime import timedelta

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    old = now - timedelta(hours=2)
    async with TestingSessionLocal() as session:
        session.add(
            Call(
                id="call-reap-1",
                namespace="test_msk",
                direction="inbound",
                call_created_at=old,
                created_at=old,
                expires_at=now,
            )
        )
        session.add(
            Job(
                id="job-reap-stalled",
                namespace="test_msk",
                call_id="call-reap-1",
                status="queued",
                progress=0,
                step="pending_download",
                idempotency_key="reap-1",
                callback_url="https://crm.example.com/hook",
                created_at=old,
            )
        )
        session.add(
            Job(
                id="job-reap-fresh",
                namespace="test_msk",
                call_id="call-reap-1",
                status="queued",
                progress=0,
                step="pending_download",
                idempotency_key="reap-2",
                created_at=now,
            )
        )
        await session.commit()

    mock_queue = MagicMock()
    mock_queue.enqueue_callback = AsyncMock(return_value="9-0")
    with (
        patch.object(ingest_mod, "get_queue", return_value=mock_queue),
        patch.object(ingest_mod, "AsyncSessionLocal", return_value=_session_cm()),
    ):
        reaped = await reap_stalled_downloads(max_age_sec=600)

    assert reaped == 1
    mock_queue.enqueue_callback.assert_awaited_once()
    failure = mock_queue.enqueue_callback.await_args[0][2]
    assert failure["status"] == "failed"
    assert failure["error"]["error"] == "download_stalled"

    async with TestingSessionLocal() as session:
        stalled = (
            await session.execute(select(Job).where(Job.id == "job-reap-stalled"))
        ).scalar_one()
        assert stalled.status == "failed"
        assert stalled.step == "download_stalled"
        assert stalled.error_details["error"] == "download_stalled"
        fresh = (await session.execute(select(Job).where(Job.id == "job-reap-fresh"))).scalar_one()
        assert fresh.status == "queued"
        assert fresh.step == "pending_download"
