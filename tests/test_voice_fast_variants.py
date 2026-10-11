import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("DEV_TOKEN", "dev-token")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("CALLBACK_SIGNING_SECRET", "test-secret-key-32-characters!!!")
os.environ.setdefault("VOICE_SEARCH_MODEL_NAME", "small")

from unittest.mock import MagicMock

import pytest

from app.api.voice import VOICE_SEARCH_HOTWORDS, VOICE_SEARCH_INITIAL_PROMPT
from app.core.config import get_settings


def _wav_bytes():
    import struct

    num_samples = 16000
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
        16000,
        32000,
        2,
        16,
        b"data",
        len(data),
    )
    return header + data


@pytest.fixture
def mock_engine(monkeypatch):
    engine = MagicMock()
    engine.transcribe.return_value = ([{"text": "тест", "confidence": 1.0}], {"duration": 1.0})
    monkeypatch.setattr("app.workers.fast_voice_search.get_voice_search_engine", lambda: engine)
    return engine


@pytest.fixture
def clean_settings(monkeypatch):
    for var in (
        "VOICE_SEARCH_BEAM_MIN",
        "VOICE_SEARCH_BEAM_SIZE",
        "VOICE_SEARCH_INITIAL_PROMPT",
        "VOICE_SEARCH_HOTWORDS",
    ):
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_defaults_preserve_behavior(mock_engine, clean_settings):
    """Дефолты новых флагов не меняют поведение: beam>=2, встроенные промпт/хотвордс."""
    from app.workers.fast_voice_search import transcribe

    transcribe({"audio_hex": _wav_bytes().hex(), "language": "ru"})
    _, kwargs = mock_engine.transcribe.call_args
    assert kwargs["beam_size"] >= 2
    assert kwargs["initial_prompt"] == VOICE_SEARCH_INITIAL_PROMPT
    assert kwargs["hotwords"] == VOICE_SEARCH_HOTWORDS
    assert kwargs["language"] == "ru"
    assert kwargs["word_timestamps"] is False


def test_beam_min_one_allows_greedy(mock_engine, clean_settings, monkeypatch):
    """VOICE_SEARCH_BEAM_MIN=1 + SIZE=1 дают beam=1 (только замер через env)."""
    from app.workers.fast_voice_search import transcribe

    monkeypatch.setenv("VOICE_SEARCH_BEAM_MIN", "1")
    monkeypatch.setenv("VOICE_SEARCH_BEAM_SIZE", "1")
    get_settings.cache_clear()
    transcribe({"audio_hex": _wav_bytes().hex(), "language": "ru"})
    _, kwargs = mock_engine.transcribe.call_args
    assert kwargs["beam_size"] == 1


def test_empty_prompt_and_hotwords_disable(mock_engine, clean_settings, monkeypatch):
    """Пустые строки отключают подсказки декодера."""
    from app.workers.fast_voice_search import transcribe

    monkeypatch.setenv("VOICE_SEARCH_INITIAL_PROMPT", "")
    monkeypatch.setenv("VOICE_SEARCH_HOTWORDS", "")
    get_settings.cache_clear()
    transcribe({"audio_hex": _wav_bytes().hex(), "language": "ru"})
    _, kwargs = mock_engine.transcribe.call_args
    assert kwargs["initial_prompt"] == ""
    assert kwargs["hotwords"] == ""
