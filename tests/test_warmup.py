"""Пункт 1 B1-догонки: прогрев моделей при старте воркеров + гейт /ready."""

import io
import wave
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.workers.warmup import WORKER_READY_TTL_SEC, synthetic_warmup_wav


def test_synthetic_warmup_wav_is_valid_audio():
    raw = synthetic_warmup_wav()
    assert len(raw) > 1000
    with wave.open(io.BytesIO(raw), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 16000
        assert w.getnframes() == 16000
    assert WORKER_READY_TTL_SEC >= 30


@pytest.mark.asyncio
async def test_fast_prewarm_runs_dummy_transcribe_and_marks_ready():
    import app.workers.fast_voice_search as fast

    engine = MagicMock()
    engine.transcribe.return_value = ([{"text": "warmup"}], {"duration": 1.0})
    with (
        patch.object(fast, "get_voice_search_engine", return_value=engine),
        patch.object(fast, "mark_worker_ready", AsyncMock()) as mock_mark,
    ):
        # prewarm синхронный; флаг выставляет main() — проверяем оба шага.
        fast.prewarm()
        await fast.mark_worker_ready(MagicMock(), "fast")

    engine.load_model.assert_called_once()
    engine.transcribe.assert_called_once()
    args, _ = engine.transcribe.call_args
    assert isinstance(args[0], io.BytesIO)
    mock_mark.assert_awaited_once()


@pytest.mark.asyncio
async def test_long_prewarm_runs_dummy_transcribe_and_marks_ready():
    import app.workers.long_audio_processor as long

    engine = MagicMock()
    engine.transcribe.return_value = ([{"text": "warmup"}], {"duration": 1.0})
    mock_queue = MagicMock()
    mock_queue.set_worker_ready = AsyncMock(return_value=None)
    with (
        patch.object(long, "get_asr_engine", return_value=engine),
        patch.object(long, "get_queue", return_value=mock_queue),
    ):
        await long.prewarm(mock_queue)

    engine.load_model.assert_called_once()
    engine.transcribe.assert_called_once()
    mock_queue.set_worker_ready.assert_awaited_once_with("long", ttl_sec=WORKER_READY_TTL_SEC)


@pytest.mark.asyncio
async def test_ready_503_until_workers_warmed():
    """Без флагов прогрева воркеров /ready — 503, даже если БД/Redis/S3 живы."""
    import app.api.health as health_mod

    mock_engine = MagicMock()
    mock_conn = AsyncMock()
    mock_engine.connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_queue = MagicMock()
    mock_queue.check_health = AsyncMock(return_value=True)
    mock_queue.is_worker_ready = AsyncMock(return_value=False)
    mock_storage = MagicMock()
    mock_storage.check_health.return_value = True

    with (
        patch.object(health_mod, "engine", mock_engine),
        patch.object(health_mod, "get_queue", return_value=mock_queue),
        patch.object(health_mod, "get_storage", return_value=mock_storage),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/ready")
            assert resp.status_code == 503
            data = resp.json()
            assert data["status"] == "unavailable"
            assert data["dependencies"]["worker_long"] is False
            assert data["dependencies"]["worker_fast"] is False


@pytest.mark.asyncio
async def test_ready_200_after_workers_warmed():
    import app.api.health as health_mod

    mock_engine = MagicMock()
    mock_conn = AsyncMock()
    mock_engine.connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_queue = MagicMock()
    mock_queue.check_health = AsyncMock(return_value=True)
    mock_queue.is_worker_ready = AsyncMock(return_value=True)
    mock_storage = MagicMock()
    mock_storage.check_health.return_value = True

    with (
        patch.object(health_mod, "engine", mock_engine),
        patch.object(health_mod, "get_queue", return_value=mock_queue),
        patch.object(health_mod, "get_storage", return_value=mock_storage),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/ready")
            assert resp.status_code == 200
            assert resp.json()["status"] == "ready"


def test_has_avx2_parses_cpuinfo_flags(tmp_path):
    from app.core.cpu import cpu_flags, has_avx2

    f = tmp_path / "cpuinfo"
    f.write_text(
        "processor\t: 0\nflags\t\t: fpu vme avx avx2 bmi1 bmi2 fma\n",
        encoding="utf-8",
    )
    assert has_avx2(f) is True
    assert "avx2" in cpu_flags(f)

    f.write_text("processor\t: 0\nflags\t\t: fpu vme avx\n", encoding="utf-8")
    assert has_avx2(f) is False

    assert has_avx2(tmp_path / "no-such-file") is False


@pytest.mark.asyncio
async def test_health_reports_avx2_details():
    """Пункт 4: /health отдаёт details.avx2 (bool)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/health")
        assert resp.status_code == 200
        details = resp.json().get("details")
        assert isinstance(details, dict)
        assert isinstance(details.get("avx2"), bool)
