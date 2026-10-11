import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("DEV_TOKEN", "dev-token")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("CALLBACK_SIGNING_SECRET", "test-secret-key-32-characters!!!")

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import get_settings
from app.core.errors import AppError
from app.llm.port import AnalysisContext


def _ctx():
    return AnalysisContext(namespace="test", call_id="call-1")


async def test_external_adapter_disabled_by_default(monkeypatch):
    """Адаптер выключен по умолчанию: 403 без HTTP-запросов."""
    from app.llm.external_adapter import ExternalProviderAdapter

    # Без cache_clear: правим общий синглтон через monkeypatch (авто-откат),
    # иначе рвётся идентичность get_settings() для других тестов.
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_external_provider_enabled", False)
    adapter = ExternalProviderAdapter()
    assert adapter.enabled is False
    with pytest.raises(AppError) as exc:
        await adapter.analyze("маскированный текст", _ctx())
    assert exc.value.status_code == 403


async def test_external_adapter_uses_configured_model(monkeypatch):
    """Модель берётся из LLM_EXTERNAL_PROVIDER_MODEL, а не захардкожена."""
    from app.llm.external_adapter import ExternalProviderAdapter

    settings = get_settings()
    monkeypatch.setattr(settings, "llm_external_provider_enabled", True)
    monkeypatch.setattr(settings, "llm_external_provider_url", "https://llm.example.com/v1")
    monkeypatch.setattr(settings, "llm_external_provider_api_key", "k")
    monkeypatch.setattr(settings, "llm_external_provider_model", "custom-model-1")
    adapter = ExternalProviderAdapter()
    assert adapter.model == "custom-model-1"

    body = {
        "choices": [
            {
                "message": {
                    "content": (
                        '{"summary": "s", "call_result": "info_provided", '
                        '"inquiry_type": "other", "entities": {}, '
                        '"agreements": [], "sentiment": {}, '
                        '"manager_checklist": {}}'
                    )
                }
            }
        ]
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = body
    mock_resp.raise_for_status.return_value = None
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    with patch("app.llm.external_adapter.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value = mock_client
        await adapter.analyze("маскированный текст", _ctx())
    payload = mock_client.post.call_args[1]["json"]
    assert payload["model"] == "custom-model-1"
