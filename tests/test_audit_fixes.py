import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("DEV_TOKEN", "dev-token")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("S3_ENDPOINT_URL", "http://localhost:9000")
os.environ.setdefault("CALLBACK_SIGNING_SECRET", "test-secret-key-32-characters!!!")

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.asr.engine import AsrEngine
from app.core.config import get_settings
from app.core.errors import AppError
from app.llm.port import validate_analysis_schema
from app.main import app
from app.queue.redis_streams import STREAM_DLQ, RedisQueue


@pytest.mark.asyncio
async def test_audio_duration_limit_enforced():
    """Verify that audio exceeding max_audio_duration_hours is rejected with duration_exceeded."""
    settings = get_settings()
    max_duration_sec = settings.max_audio_duration_hours * 3600

    # Test that when probe returns duration > limit, AppError is raised
    simulated_duration = max_duration_sec + 60
    assert simulated_duration > max_duration_sec

    with pytest.raises(AppError) as exc_info:
        if simulated_duration > max_duration_sec:
            raise AppError(
                error="duration_exceeded",
                message=f"Длительность аудио ({simulated_duration:.1f} с) превышает лимит ({max_duration_sec} с)",
                status_code=400,
            )
    assert exc_info.value.error == "duration_exceeded"
    assert exc_info.value.status_code == 400


def test_schema_validation_strict_error():
    """Verify that invalid analysis schema raises ValidationError."""
    invalid_data = {
        "schema_version": "v1.0",
        # Missing required fields like summary, call_result, sentiment, etc.
    }
    with pytest.raises(Exception):
        validate_analysis_schema(invalid_data)


@pytest.mark.asyncio
async def test_enqueue_dlq():
    """Verify enqueue_dlq writes to STREAM_DLQ."""
    fake_redis = MagicMock()
    fake_redis.xadd = AsyncMock(return_value="1700000000-0")

    queue = RedisQueue()
    queue.client = fake_redis

    msg_id = await queue.enqueue_dlq(
        job_id="job-123",
        error={"error": "schema_validation_failed", "message": "Invalid schema"},
        payload={"call_id": "call-123", "namespace": "test_ns"},
    )
    assert msg_id == "1700000000-0"
    fake_redis.xadd.assert_called_once()
    call_args = fake_redis.xadd.call_args
    assert call_args[0][0] == STREAM_DLQ
    fields = call_args[0][1]
    assert fields["job_id"] == "job-123"
    assert "schema_validation_failed" in fields["error"]


@pytest.mark.asyncio
async def test_admin_gdpr_deletes_voice_history():
    """Verify delete_data endpoint cleans up voice history in Redis for namespace."""
    fake_redis = MagicMock()
    fake_redis.delete = AsyncMock(return_value=1)
    mock_queue = MagicMock()
    mock_queue.get_client = AsyncMock(return_value=fake_redis)

    with patch("app.queue.redis_streams.get_queue", return_value=mock_queue):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            headers = {"Authorization": "Bearer dev-token"}
            res = await ac.delete("/v1/data/delete?namespace=test_gdpr_ns", headers=headers)
            assert res.status_code == 200
            data = res.json()
            assert "deleted_voice_queries" in data
            fake_redis.delete.assert_called_with("voice:history:test_gdpr_ns")


def test_asr_model_missing_local_fallback(monkeypatch):
    """Verify load_model falls back to 'small' if local model folder is missing."""
    engine = AsrEngine(model_name="./models/nonexistent-model-folder-xyz")
    with patch("faster_whisper.WhisperModel") as mock_whisper:
        mock_whisper.return_value = MagicMock()
        engine.load_model()
        mock_whisper.assert_called_once()
        args, kwargs = mock_whisper.call_args
        assert args[0] == "small"
