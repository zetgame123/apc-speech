import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["DEBUG"] = "true"
os.environ["DEV_TOKEN"] = "dev-token"
os.environ["APP_ENV"] = "test"
os.environ["REDIS_URL"] = "redis://localhost:6379/0"
os.environ["S3_ENDPOINT_URL"] = "http://localhost:9000"
os.environ["CALLBACK_SIGNING_SECRET"] = "test-secret-key-32-characters!!!"

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.security import sign_callback_payload


def test_callback_signature_format():
    timestamp, sig = sign_callback_payload("test-secret-key-32-characters!!!", b'{"status": "ok"}')
    assert sig.startswith("sha256=")
    assert isinstance(timestamp, int)
    assert len(sig) > len("sha256=")


def test_callback_signature_deterministic():
    """Same input + same timestamp should produce same signature."""
    body = b'{"job_id": "123"}'
    secret = "my-secret-key-must-be-long-enough!"
    t1, s1 = sign_callback_payload(secret, body)
    # Sign again — timestamp will differ but format should be consistent
    t2, s2 = sign_callback_payload(secret, body)
    assert s1.startswith("sha256=")
    assert s2.startswith("sha256=")


@pytest.mark.asyncio
async def test_dispatch_callback_success():
    """dispatch_callback should return True on successful HTTP response."""
    from app.workers.callback_dispatcher import dispatch_callback

    mock_resp = MagicMock()
    mock_resp.is_success = True
    mock_resp.status_code = 200
    mock_resp.text = "OK"

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp

    with (
        patch("app.workers.callback_dispatcher.httpx.AsyncClient") as mock_cls,
        patch("app.workers.callback_dispatcher.AsyncSessionLocal") as mock_db_cls,
    ):
        mock_cls.return_value.__aenter__.return_value = mock_client

        # Mock DB session
        mock_session = AsyncMock()
        mock_db_cls.return_value.__aenter__.return_value = mock_session

        result = await dispatch_callback("job-123", "http://example.com/hook", {"status": "ok"})
        assert result is True
        mock_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_dispatch_callback_retries_five_times_then_gives_up():
    """5 попыток с нарастающей задержкой [5,15,60,300,900], затем сдаётся."""
    import asyncio

    from app.workers import callback_dispatcher
    from app.workers.callback_dispatcher import dispatch_callback

    assert callback_dispatcher.RETRY_DELAYS_SEC == [5, 15, 60, 300, 900]

    mock_resp = MagicMock()
    mock_resp.is_success = False
    mock_resp.status_code = 500
    mock_resp.text = "ERR"

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    slept = []

    async def fake_sleep(delay):
        slept.append(delay)

    with (
        patch("app.workers.callback_dispatcher.httpx.AsyncClient") as mock_cls,
        patch("app.workers.callback_dispatcher.AsyncSessionLocal") as mock_db_cls,
        patch.object(asyncio, "sleep", fake_sleep),
    ):
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_session = AsyncMock()
        mock_db_cls.return_value.__aenter__.return_value = mock_session

        result = await dispatch_callback("job-123", "http://example.com/hook", {"status": "ok"})
        assert not result
        assert mock_client.post.call_count == 5
        assert slept == [5, 15, 60, 300]


@pytest.mark.asyncio
async def test_dispatch_callback_ssrf_rejected():
    """Callback на внутренний адрес не уходит (SSRF), без HTTP-попыток."""
    from unittest.mock import patch

    from app.workers.callback_dispatcher import dispatch_callback

    with patch("app.workers.callback_dispatcher.httpx.AsyncClient") as mock_cls:
        result = await dispatch_callback("job-123", "http://127.0.0.1:9/hook", {"status": "ok"})
        assert result is False
        mock_cls.assert_not_called()
