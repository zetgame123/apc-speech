import json
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("DEV_TOKEN", "dev-token")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("CALLBACK_SIGNING_SECRET", "test-secret-key-32-characters!!!")

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import get_settings
from app.queue import redis_streams
from app.queue.redis_streams import RedisQueue


@pytest.fixture
def fresh_queue():
    get_settings.cache_clear()
    yield RedisQueue()
    get_settings.cache_clear()


async def test_socket_timeout_exceeds_voice_window_default(fresh_queue, monkeypatch):
    """Без явного socket_timeout redis-py 8.x рвёт BLPOP ~через 5 с (TimeoutError),
    и быстрый путь отвечает 503 при любом окне >5 с. Проверяем запас +5 с."""
    monkeypatch.delenv("REDIS_SOCKET_TIMEOUT_SEC", raising=False)
    monkeypatch.delenv("VOICE_SEARCH_RESULT_TIMEOUT_SEC", raising=False)
    get_settings.cache_clear()
    with patch.object(redis_streams.aioredis, "from_url") as mock_from_url:
        mock_from_url.return_value = MagicMock()
        await fresh_queue.get_client()
    _, kwargs = mock_from_url.call_args
    window = get_settings().voice_search_result_timeout_sec
    assert kwargs["socket_timeout"] >= window + 5.0
    assert kwargs["socket_timeout"] > 5.0


async def test_socket_timeout_follows_raised_voice_window(fresh_queue, monkeypatch):
    """Диагностическое окно 30 с тоже должно укладываться в таймаут сокета."""
    monkeypatch.setenv("VOICE_SEARCH_RESULT_TIMEOUT_SEC", "30")
    monkeypatch.delenv("REDIS_SOCKET_TIMEOUT_SEC", raising=False)
    get_settings.cache_clear()
    with patch.object(redis_streams.aioredis, "from_url") as mock_from_url:
        mock_from_url.return_value = MagicMock()
        await fresh_queue.get_client()
    _, kwargs = mock_from_url.call_args
    assert kwargs["socket_timeout"] >= 35.0


async def test_wait_fast_result_timeout_returns_none(fresh_queue):
    """Серверный BLPOP-таймаут (None) — это штатный 503 voice_search_timeout,
    а не исключение очереди."""
    fake_client = AsyncMock()
    fake_client.blpop.return_value = None
    fresh_queue.client = fake_client
    result = await fresh_queue.wait_fast_result("req-1", 2.4)
    assert result is None
    fake_client.blpop.assert_called_once()


async def test_wait_fast_result_returns_payload(fresh_queue):
    payload = {"segments": [{"text": "тест"}], "metadata": {"duration": 3.0}}
    fake_client = AsyncMock()
    fake_client.blpop.return_value = ("voice:result:req-2", json.dumps(payload))
    fresh_queue.client = fake_client
    result = await fresh_queue.wait_fast_result("req-2", 30.0)
    assert result == payload
    fake_client.delete.assert_called_once_with("voice:result:req-2")
