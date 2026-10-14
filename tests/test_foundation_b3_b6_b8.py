"""Пункт 5: недостающие подтверждающие тесты для B3/B4/B5/B6/B8.

B3: /ready (200 с моками, 503 без зависимостей), эхо/генерация X-Request-Id.
B4: единый формат ошибок при 400 (битый JSON) и 422 (валидация), без PII в details.
B5: read-токен не может писать (403 на write-ручке).
B6: 413/400 лимиты voice и upload через HTTP; лимит числа заданий -> 429 + Retry-After.
B8: враждебный вход (пустой/переразмер/битый файл); запрет shell-вызовов в app/.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("DEV_TOKEN", "dev-token")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("S3_ENDPOINT_URL", "http://localhost:9000")
os.environ.setdefault("CALLBACK_SIGNING_SECRET", "test-secret-key-32-characters!!!")

import struct
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
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
    """Моки S3/очереди/rate limit, как в test_jobs_api."""
    mock_storage = MagicMock()
    mock_storage.upload_bytes.return_value = None
    mock_storage.upload_file.return_value = None
    mock_storage.ensure_buckets.return_value = None
    mock_storage.head_object.return_value = {"ContentLength": 1024, "ContentType": "audio/wav"}
    monkeypatch.setattr("app.api.jobs.get_storage", lambda: mock_storage)

    mock_queue = MagicMock()
    mock_queue.enqueue_long_job = AsyncMock(return_value=None)
    mock_queue.ensure_groups = AsyncMock(return_value=None)
    mock_queue.get_client = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr("app.api.jobs.get_queue", lambda: mock_queue)

    fake_redis = MagicMock()
    fake_redis.incr = AsyncMock(return_value=1)
    fake_redis.expire = AsyncMock(return_value=None)
    mock_rl_queue = MagicMock()
    mock_rl_queue.get_client = AsyncMock(return_value=fake_redis)
    monkeypatch.setattr("app.api.rate_limit.get_queue", lambda: mock_rl_queue)
    return mock_storage, mock_queue


# --- B3 ---


@pytest.mark.asyncio
async def test_ready_reports_unavailable_without_dependencies():
    """Без запущенных БД/Redis/S3 — 503 со статусом unavailable."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/ready")
        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "unavailable"
        assert set(data["dependencies"]) == {
            "database",
            "redis",
            "s3",
            "worker_long",
            "worker_fast",
        }


@pytest.mark.asyncio
async def test_ready_ok_with_healthy_dependencies():
    """Все зависимости доступны — 200 ready."""
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
            data = resp.json()
            assert data["status"] == "ready"
            assert all(data["dependencies"].values())


@pytest.mark.asyncio
async def test_x_request_id_echoed_and_generated():
    """Заголовок принимается от клиента или генерируется, всегда возвращается."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/health", headers={"X-Request-Id": "client-req-1"})
        assert resp.status_code == 200
        assert resp.headers["X-Request-Id"] == "client-req-1"

        resp2 = await ac.get("/health")
        generated = resp2.headers.get("X-Request-Id")
        assert generated, "сервис обязан вернуть X-Request-Id"
        assert generated != "client-req-1"


# --- B4 ---


@pytest.mark.asyncio
async def test_malformed_json_returns_400_envelope():
    """Битый JSON — 400 bad_request в единой форме {error, message, details}."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/v1/jobs/by-url",
            content=b'{"namespace": broken',
            headers={"Authorization": "Bearer dev-token", "Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"] == "bad_request"
        assert isinstance(data["message"], str) and data["message"]
        assert isinstance(data["details"], dict)


@pytest.mark.asyncio
async def test_validation_error_returns_422_envelope_without_pii():
    """Валидация — 422, в details нет сырых input/ctx (санитизация от PII)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/v1/jobs/by-url",
            json={"namespace": "test_msk", "url": "not-a-url"},
            headers={"Authorization": "Bearer dev-token"},
        )
        assert resp.status_code in (400, 422)
        data = resp.json()
        assert set(data) == {"error", "message", "details"}
        raw = resp.text
        assert '"input"' not in raw and '"ctx"' not in raw


# --- B5 ---


@pytest.mark.asyncio
async def test_read_scope_token_cannot_create_jobs(monkeypatch):
    """Токен только с read — 403 forbidden_scope на write-ручке."""
    from app.core.security import hash_token

    token = "read-only-token-12345"
    settings = get_settings()
    monkeypatch.setattr(settings, "api_tokens_hashed", f"{hash_token(token)}:read:test_msk")
    monkeypatch.setattr(settings, "dev_token", None)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/v1/jobs/by-s3",
            json={"namespace": "test_msk", "s3_key": "x.wav"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "forbidden_scope"


# --- B6 ---


@pytest.mark.asyncio
async def test_upload_oversize_returns_413_envelope(mock_infra, monkeypatch):
    """Файл сверх лимита — 413 в единой форме, до обращения к S3."""
    settings = get_settings()
    monkeypatch.setattr(settings, "max_upload_size_mb", 0)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/v1/jobs/upload",
            files={"file": ("big.wav", make_wav_bytes(), "audio/wav")},
            data={"namespace": "test_msk"},
            headers={"Authorization": "Bearer dev-token"},
        )
        assert resp.status_code == 413
        assert resp.json()["error"] == "payload_too_large"


@pytest.mark.asyncio
async def test_upload_empty_returns_400(mock_infra):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/v1/jobs/upload",
            files={"file": ("empty.wav", b"", "audio/wav")},
            data={"namespace": "test_msk"},
            headers={"Authorization": "Bearer dev-token"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "empty_file"


@pytest.mark.asyncio
async def test_voice_oversize_returns_413():
    """Голосовой запрос сверх 10 МБ — 413 до ASR."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/v1/voice/search?namespace=test_msk",
            content=b"x" * (10 * 1024 * 1024 + 1),
            headers={"Authorization": "Bearer dev-token", "Content-Type": "audio/wav"},
        )
        assert resp.status_code == 413
        assert resp.json()["error"] == "payload_too_large"


@pytest.mark.asyncio
async def test_voice_empty_returns_400():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/v1/voice/search?namespace=test_msk",
            content=b"",
            headers={"Authorization": "Bearer dev-token", "Content-Type": "audio/wav"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "empty_audio"


@pytest.mark.asyncio
async def test_job_capacity_limit_returns_429_with_retry_after(mock_infra, monkeypatch):
    """Лимит числа заданий: сверх MAX_PENDING_JOBS — 429 + Retry-After."""
    settings = get_settings()
    monkeypatch.setattr(settings, "max_pending_jobs", 1)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        headers = {"Authorization": "Bearer dev-token"}
        first = await ac.post(
            "/v1/jobs/upload",
            files={"file": ("a.wav", make_wav_bytes(duration_sec=1), "audio/wav")},
            data={"namespace": "test_cap"},
            headers=headers,
        )
        assert first.status_code == 202

        # Другое содержимое (иначе сработает дедупликация по хэшу и вернётся 200).
        second = await ac.post(
            "/v1/jobs/upload",
            files={"file": ("b.wav", make_wav_bytes(duration_sec=2), "audio/wav")},
            data={"namespace": "test_cap"},
            headers=headers,
        )
        assert second.status_code == 429
        assert second.json()["error"] == "too_many_jobs"
        assert "Retry-After" in second.headers


# --- B8 ---


@pytest.mark.asyncio
async def test_upload_corrupt_audio_rejected_by_probe(mock_infra):
    """Битый файл, притворяющийся wav, — 4xx, а не 202."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/v1/jobs/upload",
            files={"file": ("fake.wav", b"NOT-AUDIO" * 1000, "audio/wav")},
            data={"namespace": "test_msk"},
            headers={"Authorization": "Bearer dev-token"},
        )
        assert resp.status_code in (400, 413, 422)


def make_empty_wav_bytes(sample_rate=16000):
    """Корректный заголовок WAV без данных: потоков ffprobe хватает, duration нет."""
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36,
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
        0,
    )


@pytest.mark.asyncio
async def test_upload_garbage_rejected_with_invalid_audio_envelope(mock_infra):
    """Мусор под видом wav — ровно 400 invalid_audio в единой форме (fail-closed)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/v1/jobs/upload",
            files={"file": ("fake.wav", b"NOT-AUDIO" * 1000, "audio/wav")},
            data={"namespace": "test_probe_garbage"},
            headers={"Authorization": "Bearer dev-token"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert set(data) == {"error", "message", "details"}
        assert data["error"] == "invalid_audio"
        assert isinstance(data["message"], str) and data["message"]


@pytest.mark.asyncio
async def test_upload_empty_container_rejected(mock_infra):
    """Пустой контейнер (заголовок без данных, duration нет) — 400 invalid_audio."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/v1/jobs/upload",
            files={"file": ("empty.wav", make_empty_wav_bytes(), "audio/wav")},
            data={"namespace": "test_probe_empty"},
            headers={"Authorization": "Bearer dev-token"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert set(data) == {"error", "message", "details"}
        assert data["error"] == "invalid_audio"


@pytest.mark.asyncio
async def test_upload_truncated_wav_rejected(mock_infra):
    """Обрезанный wav (половина байт) — 400 invalid_audio, а не 202."""
    full = make_wav_bytes(duration_sec=1)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/v1/jobs/upload",
            files={"file": ("cut.wav", full[: len(full) // 2], "audio/wav")},
            data={"namespace": "test_probe_truncated"},
            headers={"Authorization": "Bearer dev-token"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert set(data) == {"error", "message", "details"}
        assert data["error"] == "invalid_audio"


@pytest.mark.asyncio
async def test_upload_valid_wav_accepted_as_control(mock_infra):
    """Положительный контроль: валидный wav той же генерации — 202."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/v1/jobs/upload",
            files={"file": ("ok.wav", make_wav_bytes(duration_sec=1), "audio/wav")},
            data={"namespace": "test_probe_control"},
            headers={"Authorization": "Bearer dev-token"},
        )
        assert resp.status_code == 202


def test_probe_audio_rejects_bad_probe_output(monkeypatch):
    """Ветки разбора ffprobe-вывода: нет потоков / нет аудио / duration нет, N/A, 0.

    Моки subprocess: ffprobe отдаёт заданный JSON, декодер успешен — отказ
    обязан прийти именно из валидации заголовков, одинаково на любой версии.
    """
    import json as jsonlib

    from app.asr import preprocessor as pre_mod
    from app.core.errors import AppError

    audio_stream = {
        "codec_name": "pcm_s16le",
        "codec_type": "audio",
        "channels": 1,
        "sample_rate": "16000",
    }
    variants = {
        "no_streams": {"streams": [], "format": {"duration": "1.000000"}},
        "video_only": {
            "streams": [{"codec_name": "png", "codec_type": "video"}],
            "format": {"duration": "1.000000"},
        },
        "duration_missing": {"streams": [audio_stream], "format": {}},
        "duration_na": {"streams": [audio_stream], "format": {"duration": "N/A"}},
        "duration_zero": {"streams": [audio_stream], "format": {"duration": "0.000000"}},
    }
    good_decode = MagicMock()
    good_decode.returncode = 0

    for name, payload in variants.items():
        probe_out = MagicMock()
        probe_out.stdout = jsonlib.dumps(payload)
        probe_out.returncode = 0

        def fake_run(cmd, **kwargs):
            return probe_out if cmd[0] == "ffprobe" else good_decode

        monkeypatch.setattr(pre_mod.subprocess, "run", fake_run)
        with pytest.raises(AppError) as exc_info:
            pre_mod.AudioPreprocessor().probe_audio("/tmp/whatever.wav")
        assert exc_info.value.error == "invalid_audio", name
        assert exc_info.value.status_code == 400, name

    # Положительный контроль мока: корректный вывод даёт словарь, а не исключение.
    probe_out = MagicMock()
    probe_out.stdout = jsonlib.dumps(
        {"streams": [audio_stream], "format": {"duration": "1.000000"}}
    )
    probe_out.returncode = 0

    def fake_run_ok(cmd, **kwargs):
        return probe_out if cmd[0] == "ffprobe" else good_decode

    monkeypatch.setattr(pre_mod.subprocess, "run", fake_run_ok)
    info = pre_mod.AudioPreprocessor().probe_audio("/tmp/whatever.wav")
    assert info["duration"] == 1.0
    assert info["channels"] == 1


@pytest.mark.asyncio
async def test_upload_without_ffprobe_fails_closed(mock_infra, monkeypatch, tmp_path):
    """Без ffprobe в PATH загрузка отклоняется 4xx единой формой, а не 202.

    Fail-closed: если валидатор недоступен, файл нельзя признать годным
    и тащить в S3/очередь — отказываем сразу (B8). Файл заведомо валидный
    (тот же генератор, что даёт 202 при наличии ffprobe), поэтому отказ
    доказывает именно отсутствие валидатора, а не битость входа.
    """
    valid_wav = make_wav_bytes(duration_sec=1)
    headers = {"Authorization": "Bearer dev-token"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Позитивный контроль: с ffprobe в PATH тот же валидный файл — 202.
        ok = await ac.post(
            "/v1/jobs/upload",
            files={"file": ("valid.wav", valid_wav, "audio/wav")},
            data={"namespace": "test_probe"},
            headers=headers,
        )
        assert ok.status_code == 202

        # Другое содержимое (иначе сработает дедупликация по хэшу и вернётся 200).
        other_wav = make_wav_bytes(duration_sec=2)
        monkeypatch.setenv("PATH", str(tmp_path))  # пустой каталог: ffprobe не резолвится
        resp = await ac.post(
            "/v1/jobs/upload",
            files={"file": ("valid2.wav", other_wav, "audio/wav")},
            data={"namespace": "test_probe"},
            headers=headers,
        )
        assert resp.status_code in (400, 413, 422)
        data = resp.json()
        assert set(data) == {"error", "message", "details"}
        assert isinstance(data["message"], str) and data["message"]


@pytest.mark.asyncio
async def test_voice_queue_unavailable_returns_503_with_retry_after(monkeypatch):
    """Очередь голосового поиска недоступна — 503 + Retry-After (B4/B6)."""
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "voice_search_use_worker", True)

    fake_redis = MagicMock()
    fake_redis.incr = AsyncMock(return_value=1)
    fake_redis.expire = AsyncMock(return_value=None)
    mock_rl_queue = MagicMock()
    mock_rl_queue.get_client = AsyncMock(return_value=fake_redis)
    monkeypatch.setattr("app.api.rate_limit.get_queue", lambda: mock_rl_queue)

    mock_queue = MagicMock()
    mock_queue.enqueue_fast_job = AsyncMock(side_effect=ConnectionError("redis down"))
    monkeypatch.setattr("app.api.voice.get_queue", lambda: mock_queue)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/v1/voice/search?namespace=test_msk",
            content=make_wav_bytes(),
            headers={"Authorization": "Bearer dev-token", "Content-Type": "audio/wav"},
        )
        assert resp.status_code == 503
        assert resp.json()["error"] == "voice_search_unavailable"
        assert "Retry-After" in resp.headers


def test_sigterm_handlers_stop_workers_gracefully():
    """B6: SIGTERM переводит воркеры в остановку без потери флага цикла."""
    import app.workers.callback_dispatcher as cb
    import app.workers.fast_voice_search as fast
    import app.workers.long_audio_processor as long

    for mod in (long, fast, cb):
        assert mod.is_running is True
        mod.handle_sigterm()
        assert mod.is_running is False
        mod.is_running = True  # вернуть для других тестов


def test_no_shell_invocations_in_app():
    """ffmpeg и прочие процессы — только argv-массивом: shell=True/os.system запрещены."""
    import ast

    root = Path(__file__).resolve().parents[1] / "app"
    offenders = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = ""
                if isinstance(func, ast.Attribute):
                    name = func.attr
                elif isinstance(func, ast.Name):
                    name = func.id
                if name in {"system", "popen", "spawnlp", "spawnlpe"}:
                    offenders.append(f"{path}:{node.lineno} {name}")
                    continue
                if name in {"run", "call", "check_call", "check_output", "Popen"}:
                    for kw in node.keywords:
                        if kw.arg == "shell" and not (
                            isinstance(kw.value, ast.Constant) and kw.value.value is False
                        ):
                            offenders.append(f"{path}:{node.lineno} shell=")
    assert not offenders, f"shell-вызовы в приложении: {offenders}"


@pytest.mark.asyncio
async def test_unhandled_exception_returns_500_envelope_without_leak(monkeypatch):
    """Пункт 2: 500 — единая форма, без внутренних деталей и PII вне debug."""
    import app.api.health as health_mod

    secret = "phone=+79990001122 card=4111111111111111 db_password=s3cr3t"
    # raise_app_exceptions=False: иначе тестовый клиент перевыбрасывает
    # исключение вместо возврата 500-ответа от хендлера.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    with patch.object(
        health_mod,
        "get_settings",
        side_effect=RuntimeError(f"boom {secret}"),
    ):
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/health")
            assert resp.status_code == 500
            data = resp.json()
            assert set(data) == {"error", "message", "details"}
            assert data["error"] == "internal_server_error"
            assert isinstance(data["message"], str) and data["message"]

    # Production-режим (debug=False): деталей исключения нет, секрета в теле нет.
    settings = get_settings()
    monkeypatch.setattr(settings, "debug", False)
    with patch.object(
        health_mod,
        "get_settings",
        side_effect=RuntimeError(f"boom {secret}"),
    ):
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/health")
            assert resp.status_code == 500
            assert resp.json()["details"] == {}
            assert "s3cr3t" not in resp.text
            assert "+79990001122" not in resp.text
