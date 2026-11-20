from datetime import datetime, timedelta, timezone
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.analytics import _naive as analytics_naive
from app.api.calls import _naive as calls_naive
from app.asr.normalizer import get_normalizer
from app.core.config import get_settings
from app.db.models import AudioObject, Call
from app.llm.external_adapter import ExternalProviderAdapter
from app.llm.factory import get_llm_adapter
from app.llm.local_adapter import LocalLlamaCppAdapter
from app.main import app
from app.workers.fast_voice_search import _decode_audio_bytes


def test_naive_converts_non_utc_offset_to_utc():
    """Aware datetimes with non-zero timezone offset must convert to UTC before stripping tzinfo."""
    msk_tz = timezone(timedelta(hours=3))
    dt_msk = datetime(2026, 10, 14, 15, 30, 0, tzinfo=msk_tz)
    expected_utc_naive = datetime(2026, 10, 14, 12, 30, 0)

    assert calls_naive(dt_msk) == expected_utc_naive
    assert analytics_naive(dt_msk) == expected_utc_naive


def test_get_normalizer_is_cached_singleton():
    n1 = get_normalizer()
    n2 = get_normalizer()
    assert n1 is n2


def test_llm_factory_selects_adapter_by_flag(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_external_provider_enabled", False)
    assert isinstance(get_llm_adapter(), LocalLlamaCppAdapter)

    monkeypatch.setattr(settings, "llm_external_provider_enabled", True)
    assert isinstance(get_llm_adapter(), ExternalProviderAdapter)


def test_fast_voice_decode_supports_base64_and_legacy_hex():
    import base64

    raw = b"RIFF1234WAVE"
    assert _decode_audio_bytes({"audio_b64": base64.b64encode(raw).decode("ascii")}) == raw
    assert _decode_audio_bytes({"audio_hex": raw.hex()}) == raw


@pytest.mark.asyncio
async def test_analyze_texts_clusters_only_once_on_masked_texts(monkeypatch):
    fake_redis = MagicMock()
    fake_redis.incr = AsyncMock(return_value=1)
    fake_redis.expire = AsyncMock(return_value=None)
    fake_redis.sismember = AsyncMock(return_value=False)
    mock_queue = MagicMock()
    mock_queue.get_client = AsyncMock(return_value=fake_redis)
    monkeypatch.setattr("app.api.rate_limit.get_queue", lambda: mock_queue)
    monkeypatch.setattr("app.core.security.get_queue", lambda: mock_queue)

    with patch("app.api.analytics.clustering_engine.fit_transform_texts") as mock_fit:
        mock_fit.return_value = ([0, 0], {0: "щиты"}, None)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/texts/analyze",
                headers={"Authorization": "Bearer dev-token"},
                json={
                    "namespace": "test_ns",
                    "texts": [
                        "Позвоните +7 999 123-45-67 по щитам",
                        "Сколько стоит щит 3х6",
                    ],
                },
            )
            assert resp.status_code == 200
            assert mock_fit.call_count == 1
            called_texts = mock_fit.call_args[0][0]
            assert "+7 999 123-45-67" not in called_texts[0]


@pytest.mark.asyncio
async def test_get_call_audio_streams_with_range_support(monkeypatch):
    from tests.conftest import TestingSessionLocal

    fake_redis = MagicMock()
    fake_redis.sismember = AsyncMock(return_value=False)
    mock_queue = MagicMock()
    mock_queue.get_client = AsyncMock(return_value=fake_redis)
    monkeypatch.setattr("app.core.security.get_queue", lambda: mock_queue)

    async with TestingSessionLocal() as db:
        call = Call(id="call-stream-1", namespace="test_ns")
        audio = AudioObject(
            id="audio-stream-1",
            call_id="call-stream-1",
            namespace="test_ns",
            s3_bucket="raw-audio",
            s3_key="test_ns/call.wav",
            mime_type="audio/wav",
            file_size_bytes=10,
        )
        db.add(call)
        db.add(audio)
        await db.commit()

    class FakeBody:
        def __init__(self, data: bytes):
            self._buf = BytesIO(data)

        def iter_chunks(self, chunk_size: int = 65536):
            while True:
                chunk = self._buf.read(chunk_size)
                if not chunk:
                    break
                yield chunk

        def close(self):
            self._buf.close()

    mock_storage = MagicMock()
    mock_storage.get_object_stream.return_value = {
        "body": FakeBody(b"01234"),
        "content_length": 5,
        "content_range": "bytes 0-4/10",
        "content_type": "audio/wav",
        "status_code": 206,
    }
    monkeypatch.setattr("app.api.calls.get_storage", lambda: mock_storage)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            "/v1/calls/call-stream-1/audio?namespace=test_ns",
            headers={"Authorization": "Bearer dev-token", "Range": "bytes=0-4"},
        )
        assert resp.status_code == 206
        assert resp.content == b"01234"
        assert resp.headers["accept-ranges"] == "bytes"
        assert resp.headers["content-range"] == "bytes 0-4/10"
        mock_storage.get_object_stream.assert_called_once_with(
            "raw-audio", "test_ns/call.wav", "bytes=0-4"
        )


@pytest.mark.asyncio
async def test_integration_auth_rejects_revoked_token(monkeypatch):
    from app.core.security import hash_token

    token = "revoked-integration-token"
    token_hash = hash_token(token)
    settings = get_settings()
    monkeypatch.setattr(settings, "api_tokens_hashed", f"{token_hash}:write+read:test_ns")

    fake_redis = MagicMock()
    fake_redis.sismember = AsyncMock(return_value=True)
    mock_queue = MagicMock()
    mock_queue.get_client = AsyncMock(return_value=fake_redis)
    monkeypatch.setattr("app.queue.redis_streams.get_queue", lambda: mock_queue)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            f"/v1/integrations/bitrix24/webhook?token={token}&namespace=test_ns",
            json={"event": "ONVOXIMPLANTCALLFINISHED"},
        )
        assert resp.status_code == 401
        assert resp.json()["error"] == "token_revoked"
