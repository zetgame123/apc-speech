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


class FakeRedis:
    """In-memory Redis mock that tracks request counts per key."""

    def __init__(self):
        self._store: dict[str, int] = {}
        self._lists: dict[str, list[str]] = {}

    async def incr(self, key: str) -> int:
        self._store[key] = self._store.get(key, 0) + 1
        return self._store[key]

    async def expire(self, key: str, ttl: int) -> None:
        pass

    async def lpush(self, key: str, value: str) -> None:
        self._lists.setdefault(key, []).insert(0, value)

    async def ltrim(self, key: str, start: int, end: int) -> None:
        pass

    async def sismember(self, key: str, value: str) -> bool:
        return False


@pytest.fixture
def mock_rate_limit_deps(monkeypatch):
    """Mock ASR, preprocessor, and rate limiter with shared FakeRedis.

    Детерминированность: состояние лимитера сбрасывается между тестами
    (свежий FakeRedis на каждый тест), а часы заморожены (фиксированный
    time.time на границе окна). Иначе переход 10-секундного окна посреди
    теста менял ключ `rate_limit:...:<window>` и 6-й запрос возвращал 200
    вместо 429 — флак `test_rate_limit_blocks_excess`
    (см. docs/known-issues.md п.22).
    """
    fake_redis = FakeRedis()

    from app.core.config import get_settings

    monkeypatch.setenv("RATE_LIMIT_REQUESTS_PER_WINDOW", "5")
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SEC", "10")
    get_settings.cache_clear()
    get_settings().voice_search_use_worker = False

    # Управляемые часы: начало 10-секундного окна, внутри теста время не идёт.
    import app.api.rate_limit as rate_limit_module

    monkeypatch.setattr(rate_limit_module.time, "time", lambda: 1_700_000_000.0)

    mock_queue = MagicMock()
    mock_queue.get_client = AsyncMock(return_value=fake_redis)
    monkeypatch.setattr("app.api.rate_limit.get_queue", lambda: mock_queue)
    # The voice-search endpoint also persists history to Redis via a background task;
    # mock it with the same in-memory fake so tests don't hit a real/unreachable Redis.
    monkeypatch.setattr("app.api.voice.get_queue", lambda: mock_queue)
    # verify_auth() checks token revocation in Redis on every request; mock it too,
    # otherwise each request pays a real connection-timeout penalty against an
    # unreachable Redis, which was silently inflating per-request latency in tests.
    monkeypatch.setattr("app.core.security.get_queue", lambda: mock_queue)

    # Mock ASR engine
    mock_engine = MagicMock()
    mock_engine.transcribe.return_value = (
        [{"text": "test", "start": 0.0, "end": 1.0, "confidence": 0.99, "words": []}],
        {"language": "ru", "language_probability": 0.99, "duration": 1.0},
    )
    mock_engine.load_model.return_value = None
    monkeypatch.setattr("app.api.voice.get_voice_search_engine", lambda: mock_engine)

    # Mock preprocessor
    mock_preprocessor = MagicMock()
    mock_preprocessor.preprocess_memory.return_value = make_wav_bytes()
    monkeypatch.setattr("app.api.voice.AudioPreprocessor", lambda: mock_preprocessor)

    return fake_redis


@pytest.mark.asyncio
async def test_rate_limit_allows_normal_requests(mock_rate_limit_deps):
    """5 requests within the window should all succeed."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        wav_data = make_wav_bytes()
        headers = {"Authorization": "Bearer dev-token", "Content-Type": "audio/wav"}

        for i in range(5):
            response = await ac.post(
                "/v1/voice/search?namespace=test",
                content=wav_data,
                headers=headers,
            )
            assert response.status_code == 200, f"Request {i + 1} failed: {response.status_code}"


@pytest.mark.asyncio
async def test_rate_limit_blocks_excess(mock_rate_limit_deps):
    """6th request in the same window should get 429 with Retry-After."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        wav_data = make_wav_bytes()
        headers = {"Authorization": "Bearer dev-token", "Content-Type": "audio/wav"}

        # First 5 should succeed
        for _ in range(5):
            response = await ac.post(
                "/v1/voice/search?namespace=test",
                content=wav_data,
                headers=headers,
            )
            assert response.status_code == 200

        # 6th should be rate-limited
        response = await ac.post(
            "/v1/voice/search?namespace=test",
            content=wav_data,
            headers=headers,
        )
        assert response.status_code == 429
        assert "Retry-After" in response.headers
