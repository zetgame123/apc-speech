"""Independent follow-up cases beyond the original audit examples."""

import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.asr.normalizer import RussianTextNormalizer
from app.core.config import get_settings
from app.llm.local_adapter import LocalLlamaCppAdapter
from app.llm.port import AnalysisContext, validate_analysis_schema
from app.main import app
from app.metrics.speech_metrics import SpeechMetricsCalculator
from app.pii.masker import PiiMasker


@pytest.mark.parametrize(
    "text",
    [
        "пришлите счет на электронную почту",
        "счет на имя клиента",
        "выставьте счет на сумму 25000 рублей",
    ],
)
def test_accounting_words_are_preserved(text):
    assert "щит" not in RussianTextNormalizer().normalize(text)


@pytest.mark.parametrize(
    "phone",
    [
        "восемь девять один два три четыре пять шесть семь восемь девять",
        "плюс семь девятьсот двенадцать триста сорок пять ноль семь ноль девять",
    ],
)
def test_spoken_phone_variations_are_masked(phone):
    result = PiiMasker().mask_text("Телефон " + phone)
    assert phone not in result
    assert "[ТЕЛЕФОН_" in result


def test_schema_rejects_unconfigured_taxonomy():
    value = (
        LocalLlamaCppAdapter()
        ._analyze_via_rules(
            "[manager] Здравствуйте", AnalysisContext(namespace="recheck", call_id="test")
        )
        .model_dump()
    )
    value["deterministic_speech_metrics"] = SpeechMetricsCalculator().compute([], 0)
    value["inquiry_type"] = "Произвольная ошибочная категория"
    with pytest.raises(Exception):
        validate_analysis_schema(value)


@pytest.mark.asyncio
async def test_malformed_json_is_bad_request():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/v1/texts/analyze", content="{bad", headers={"Content-Type": "application/json"}
        )
    assert res.status_code == 400
    assert set(res.json()) == {"error", "message", "details"}


def load_downloader():
    path = Path(__file__).resolve().parents[2] / "scripts/download_models.py"
    spec = importlib.util.spec_from_file_location("audit_model_downloader", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_model_download_failure_is_not_success(tmp_path, monkeypatch):
    module = load_downloader()
    module.QWEN_GGUF_PATH = str(tmp_path / "model.gguf")
    monkeypatch.setattr(
        module.urllib.request, "urlretrieve", MagicMock(side_effect=OSError("HTTP 404"))
    )
    with pytest.raises(Exception):
        module.download_qwen()


@pytest.mark.asyncio
async def test_production_rejects_empty_callback_secret(monkeypatch):
    import app.main as main

    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "callback_signing_secret", "")
    redis = MagicMock()
    redis.smembers = AsyncMock(return_value=set())
    queue = MagicMock()
    queue.ensure_groups = AsyncMock()
    queue.get_client = AsyncMock(return_value=redis)
    with (
        patch.object(main, "init_db", new=AsyncMock()),
        patch.object(main, "get_storage"),
        patch.object(main, "get_queue", return_value=queue),
        patch("app.asr.engine.get_voice_search_engine"),
    ):
        with pytest.raises(Exception):
            async with main.lifespan(app):
                pass
