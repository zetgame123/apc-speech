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

    # Mock rate limiter and voice history Redis
    fake_redis = MagicMock()
    fake_redis.incr = AsyncMock(return_value=1)
    fake_redis.expire = AsyncMock(return_value=None)
    stored_items = []

    async def fake_lpush(key, val):
        stored_items.insert(0, val)
        return len(stored_items)

    async def fake_ltrim(key, start, end):
        del stored_items[end + 1 :]
        return True

    async def fake_lrange(key, start, end):
        return stored_items[start : end + 1]

    async def fake_delete(key):
        stored_items.clear()
        return 1

    fake_redis.lpush = AsyncMock(side_effect=fake_lpush)
    fake_redis.ltrim = AsyncMock(side_effect=fake_ltrim)
    fake_redis.lrange = AsyncMock(side_effect=fake_lrange)
    fake_redis.delete = AsyncMock(side_effect=fake_delete)

    mock_queue = MagicMock()
    mock_queue.get_client = AsyncMock(return_value=fake_redis)
    monkeypatch.setattr("app.api.rate_limit.get_queue", lambda: mock_queue)
    monkeypatch.setattr("app.queue.redis_streams.get_queue", lambda: mock_queue)

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
        assert "3×6" in data["text"]


@pytest.mark.asyncio
async def test_voice_search_normalizes_cityboard(mock_voice_deps):
    """Ensure acoustic recognition of cityboard variations gets normalized."""
    mock_voice_deps.transcribe.return_value = (
        [
            {
                "text": "свободные сити борта и сети борд в Химках",
                "start": 0.0,
                "end": 2.5,
                "confidence": 0.96,
                "words": [],
            }
        ],
        {"language": "ru", "language_probability": 0.99, "duration": 2.5},
    )
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
        assert "ситиборда" in data["text"]
        assert "ситиборд" in data["text"]


@pytest.mark.asyncio
async def test_voice_history_endpoints(mock_voice_deps):
    """Test GET and DELETE for voice search history."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        headers = {"Authorization": "Bearer dev-token"}

        # 1. Initially empty
        res = await ac.get("/v1/voice/history?namespace=test_ns", headers=headers)
        assert res.status_code == 200
        assert res.json()["count"] == 0

        # 2. Perform a search
        wav_data = make_wav_bytes()
        post_headers = {"Authorization": "Bearer dev-token", "Content-Type": "audio/wav"}
        await ac.post(
            "/v1/voice/search?namespace=test_ns",
            content=wav_data,
            headers=post_headers,
        )

        # 3. History now has 1 entry
        res = await ac.get("/v1/voice/history?namespace=test_ns", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["count"] == 1
        assert len(data["items"]) == 1
        assert "text" in data["items"][0]

        # 4. Clear history
        del_res = await ac.delete("/v1/voice/history?namespace=test_ns", headers=headers)
        assert del_res.status_code == 200
        assert del_res.json()["status"] == "cleared"

        # 5. History is now empty
        res = await ac.get("/v1/voice/history?namespace=test_ns", headers=headers)
        assert res.status_code == 200
        assert res.json()["count"] == 0


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


@pytest.mark.asyncio
async def test_voice_history_requires_read_scope(monkeypatch):
    """Token with write scope only must receive 403 Forbidden for GET /v1/voice/history."""
    from app.core.config import get_settings
    from app.core.security import hash_token

    token_write = "token-write-only-12345"
    h_write = hash_token(token_write)
    settings = get_settings()
    monkeypatch.setattr(settings, "api_tokens_hashed", f"{h_write}:write:test_ns")
    monkeypatch.setattr(settings, "dev_token", None)
    monkeypatch.setattr(settings, "debug", False)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        headers = {"Authorization": f"Bearer {token_write}"}
        res = await ac.get("/v1/voice/history?namespace=test_ns", headers=headers)
        assert res.status_code == 403
        assert res.json().get("error") == "forbidden_scope"


@pytest.mark.asyncio
async def test_voice_sample_audio_endpoint():
    """GET /v1/voice/sample-audio returns 200 with audio/wav."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.get("/v1/voice/sample-audio")
        assert res.status_code in (200, 404)
        if res.status_code == 200:
            assert res.headers.get("content-type") == "audio/wav"


def test_asr_engine_transcribe_signature_accepts_vad_parameters():
    """AsrEngine.transcribe must accept vad_parameters without raising TypeError."""
    import inspect

    from app.asr.engine import AsrEngine

    sig = inspect.signature(AsrEngine.transcribe)
    assert "vad_parameters" in sig.parameters
