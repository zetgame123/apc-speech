import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("DEV_TOKEN", "dev-token")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("S3_ENDPOINT_URL", "http://localhost:9000")
os.environ.setdefault("CALLBACK_SIGNING_SECRET", "test-secret-key-32-characters!!!")

import struct
from unittest.mock import AsyncMock, MagicMock

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
def mock_voice_deps(monkeypatch):
    """Mock ASR engine, preprocessor, and rate limiter for voice search tests."""
    # Mock ASR engine
    mock_engine = MagicMock()
    mock_engine.transcribe.return_value = (
        [
            {
                "text": "щиты три на шесть в Балашихе",
                "start": 0.0,
                "end": 3.0,
                "confidence": 0.95,
                "words": [],
            }
        ],
        {"language": "ru", "language_probability": 0.98, "duration": 3.0},
    )
    mock_engine.load_model.return_value = None
    monkeypatch.setattr("app.api.voice.get_voice_search_engine", lambda: mock_engine)

    # Mock audio preprocessor
    mock_preprocessor = MagicMock()
    mock_preprocessor.preprocess_memory.return_value = make_wav_bytes()
    monkeypatch.setattr("app.api.voice.AudioPreprocessor", lambda: mock_preprocessor)

    # Mock rate limiter's Redis
    fake_redis = MagicMock()
    fake_redis.incr = AsyncMock(return_value=1)
    fake_redis.expire = AsyncMock(return_value=None)
    mock_queue = MagicMock()
    mock_queue.get_client = AsyncMock(return_value=fake_redis)
    monkeypatch.setattr("app.api.rate_limit.get_queue", lambda: mock_queue)

    return mock_engine


@pytest.mark.asyncio
async def test_voice_search_returns_text(mock_voice_deps):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        wav_data = make_wav_bytes()
        headers = {"Authorization": "Bearer dev-token", "Content-Type": "audio/wav"}
        response = await ac.post(
            "/v1/voice/search?namespace=test_msk",
            content=wav_data,
            headers=headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "text" in data
        assert data["language"] == "ru"
        assert data["confidence"] > 0


@pytest.mark.asyncio
async def test_voice_search_unauthorized():
    """No auth → 401."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        wav_data = make_wav_bytes()
        response = await ac.post(
            "/v1/voice/search?namespace=test_msk",
            content=wav_data,
            headers={"Content-Type": "audio/wav"},
        )
        assert response.status_code == 401
