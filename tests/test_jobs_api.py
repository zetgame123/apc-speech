import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("DEV_TOKEN", "dev-token")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("S3_ENDPOINT_URL", "http://localhost:9000")
os.environ.setdefault("CALLBACK_SIGNING_SECRET", "test-secret-key-32-characters!!!")

import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


def make_wav_bytes(duration_sec=1, sample_rate=16000):
    num_samples = sample_rate * duration_sec
    data = struct.pack(f"<{num_samples}h", *([0] * num_samples))
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(data),
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        sample_rate,
        sample_rate * 2,
        2,
        16,
        b"data",
        len(data),
    )
    return header + data


@pytest.fixture
def mock_infra(monkeypatch):
    """Mock S3, Redis queue, and rate limiter for jobs API tests."""
    # Mock storage
    mock_storage = MagicMock()
    mock_storage.upload_bytes.return_value = None
    mock_storage.upload_file.return_value = None
    mock_storage.ensure_buckets.return_value = None
    mock_storage.head_object.return_value = {"ContentLength": 1024, "ContentType": "audio/wav"}
    monkeypatch.setattr("app.api.jobs.get_storage", lambda: mock_storage)

    # Mock queue — enqueue_long_job is async
    mock_queue = MagicMock()
    mock_queue.enqueue_long_job = AsyncMock(return_value=None)
    mock_queue.ensure_groups = AsyncMock(return_value=None)
    mock_queue.get_client = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr("app.api.jobs.get_queue", lambda: mock_queue)

    # Mock rate limiter's queue
    fake_redis = MagicMock()
    fake_redis.incr = AsyncMock(return_value=1)
    fake_redis.expire = AsyncMock(return_value=None)
    mock_rl_queue = MagicMock()
    mock_rl_queue.get_client = AsyncMock(return_value=fake_redis)
    monkeypatch.setattr("app.api.rate_limit.get_queue", lambda: mock_rl_queue)

    return mock_storage, mock_queue


@pytest.mark.asyncio
async def test_create_job_upload_returns_202(mock_infra):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        wav_data = make_wav_bytes()
        headers = {"Authorization": "Bearer dev-token"}
        response = await ac.post(
            "/v1/jobs/upload",
            files={"file": ("test.wav", wav_data, "audio/wav")},
            data={"namespace": "test_msk"},
            headers=headers,
        )
        assert response.status_code == 202
        data = response.json()
        assert "job_id" in data
        assert "status_url" in data
        assert data["status"] == "queued"


@pytest.mark.asyncio
async def test_create_job_upload_unauthorized():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        wav_data = make_wav_bytes()
        response = await ac.post(
            "/v1/jobs/upload",
            files={"file": ("test.wav", wav_data, "audio/wav")},
            data={"namespace": "test_msk"},
        )
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_create_job_by_url_returns_202(mock_infra):
    with patch("app.api.jobs.validate_ssrf_url", return_value="https://example.com/audio.wav"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            headers = {"Authorization": "Bearer dev-token"}
            payload = {
                "namespace": "test_msk",
                "url": "https://example.com/audio.wav",
            }
            response = await ac.post("/v1/jobs/by-url", json=payload, headers=headers)
            assert response.status_code == 202
            data = response.json()
            assert "job_id" in data


@pytest.mark.asyncio
async def test_create_job_by_url_ssrf_blocked(mock_infra):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        headers = {"Authorization": "Bearer dev-token"}
        payload = {
            "namespace": "test_msk",
            "url": "http://127.0.0.1/audio.wav",
        }
        response = await ac.post("/v1/jobs/by-url", json=payload, headers=headers)
        assert response.status_code == 400


@pytest.mark.asyncio
async def test_get_job_not_found():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        headers = {"Authorization": "Bearer dev-token"}
        response = await ac.get(
            "/v1/jobs/00000000-0000-0000-0000-000000000000?namespace=test",
            headers=headers,
        )
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_job_upload_idempotency_conflict(mock_infra):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        wav1 = make_wav_bytes(duration_sec=1)
        wav2 = make_wav_bytes(duration_sec=2)
        headers = {"Authorization": "Bearer dev-token", "Idempotency-Key": "same-key-123"}

        # First upload
        resp1 = await ac.post(
            "/v1/jobs/upload",
            files={"file": ("test1.wav", wav1, "audio/wav")},
            data={"namespace": "test_idem"},
            headers=headers,
        )
        assert resp1.status_code == 202

        # Second upload with same key but different file content -> 409 Conflict
        resp2 = await ac.post(
            "/v1/jobs/upload",
            files={"file": ("test2.wav", wav2, "audio/wav")},
            data={"namespace": "test_idem"},
            headers=headers,
        )
        assert resp2.status_code == 409
        assert resp2.json().get("error") == "idempotency_conflict"


@pytest.mark.asyncio
async def test_create_job_upload_retry_after_failure(mock_infra):
    from sqlalchemy import update

    from app.db.models import Job
    from tests.conftest import TestingSessionLocal

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        wav = make_wav_bytes(duration_sec=1)
        headers = {"Authorization": "Bearer dev-token"}

        # 1. First upload
        resp1 = await ac.post(
            "/v1/jobs/upload",
            files={"file": ("retry_test.wav", wav, "audio/wav")},
            data={"namespace": "test_retry_ns"},
            headers=headers,
        )
        assert resp1.status_code == 202
        job_id_1 = resp1.json()["job_id"]

        # 2. Simulate job failure
        async with TestingSessionLocal() as session:
            await session.execute(
                update(Job).where(Job.id == job_id_1).values(status="failed", step="failed")
            )
            await session.commit()

        # 3. Retry upload with identical content
        resp2 = await ac.post(
            "/v1/jobs/upload",
            files={"file": ("retry_test.wav", wav, "audio/wav")},
            data={"namespace": "test_retry_ns"},
            headers=headers,
        )
        assert resp2.status_code == 202
        job_id_2 = resp2.json()["job_id"]
        assert job_id_2 != job_id_1
        assert resp2.json()["status"] == "queued"


@pytest.mark.asyncio
async def test_create_job_by_s3_returns_202(mock_infra):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        headers = {"Authorization": "Bearer dev-token"}
        payload = {
            "namespace": "test_s3_ns",
            "s3_bucket": "raw-audio",
            "s3_key": "recordings/call_123.wav",
        }
        response = await ac.post("/v1/jobs/by-s3", json=payload, headers=headers)
        assert response.status_code == 202
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "queued"


@pytest.mark.asyncio
async def test_create_job_by_s3_not_found(mock_infra):
    mock_storage, _ = mock_infra
    mock_storage.head_object.return_value = None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        headers = {"Authorization": "Bearer dev-token"}
        payload = {
            "namespace": "test_s3_ns",
            "s3_bucket": "raw-audio",
            "s3_key": "recordings/nonexistent.wav",
        }
        response = await ac.post("/v1/jobs/by-s3", json=payload, headers=headers)
        assert response.status_code == 404
        assert response.json()["error"] == "s3_object_not_found"
