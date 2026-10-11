import os
import sys

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("DEV_TOKEN", "dev-token")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("CALLBACK_SIGNING_SECRET", "test-secret-key-32-characters!!!")

from unittest.mock import MagicMock

import numpy as np
import pytest

from app.asr.tone_engine import BLANK_ID, ToneEngine, _greedy_decode


def make_wav_bytes(duration_sec=1, sample_rate=8000):
    import struct

    num_samples = int(sample_rate * duration_sec)
    data = struct.pack(f"<{num_samples}h", *([1000] * num_samples))
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
def tone_engine(monkeypatch, tmp_path):
    """ToneEngine с мокнутой ORT-сессией: все фреймы топят за букву 'а' (id 0)."""
    fake_ort = MagicMock()
    session = MagicMock()

    def fake_run(output_names, inputs):
        n_frames = 10
        lp = np.full((1, n_frames, BLANK_ID + 1), -10.0, dtype=np.float32)
        lp[:, :, 0] = 0.0
        state = np.zeros((1, 219729), dtype=np.float16)
        return [lp, state]

    session.run.side_effect = fake_run
    fake_ort.InferenceSession.return_value = session
    fake_ort.SessionOptions.return_value = MagicMock()
    monkeypatch.setitem(sys.modules, "onnxruntime", fake_ort)
    model = tmp_path / "model.onnx"
    model.write_bytes(b"fake")
    return ToneEngine(str(model))


def test_greedy_decode_collapses_repeats():
    lp = np.full((12, BLANK_ID + 1), -10.0, dtype=np.float32)
    lp[:, 0] = 0.0
    lp[5, BLANK_ID] = 0.0  # один blank-кадр
    text, conf, silence = _greedy_decode(lp)
    assert text == "а"
    assert 0.0 <= conf <= 1.0
    assert 0.0 <= silence <= 1.0


def test_transcribe_interface_shape(tone_engine):
    segments, meta = tone_engine.transcribe(make_wav_bytes(1, 8000), language="ru")
    assert isinstance(segments, list)
    assert meta["language"] == "ru"
    assert meta["duration"] == 1.0
    for seg in segments:
        assert {"start", "end", "text", "confidence", "no_speech_prob", "words"} <= set(seg)


def test_transcribe_16k_downsampled(tone_engine):
    segments, meta = tone_engine.transcribe(make_wav_bytes(1, 16000), language="ru")
    assert meta["duration"] == 1.0
    assert isinstance(segments, list)


def test_transcribe_rejects_unsupported_rate(tone_engine):
    with pytest.raises(ValueError, match="не поддерживается"):
        tone_engine.transcribe(make_wav_bytes(1, 44100), language="ru")


def test_missing_weights_raise_helpful_error(monkeypatch):
    import app.asr.tone_engine as te

    monkeypatch.setitem(sys.modules, "onnxruntime", MagicMock())
    with pytest.raises(FileNotFoundError, match="model-downloader"):
        te.ToneEngine("/nonexistent-dir/model.onnx")


def test_default_engine_is_whisper(monkeypatch):
    """Дефолт VOICE_SEARCH_ENGINE=whisper: основной путь не тронут."""
    from app.core.config import get_settings

    for var in ("VOICE_SEARCH_ENGINE",):
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    try:
        assert get_settings().voice_search_engine == "whisper"
    finally:
        get_settings.cache_clear()


def test_tone_ort_options_applied(monkeypatch, tmp_path):
    """Ручки ORT прокидываются в SessionOptions; мусор в GRAPH_OPT отклоняется."""
    import sys
    from unittest.mock import MagicMock

    import app.asr.tone_engine as te

    fake_ort = MagicMock()
    fake_ort.GraphOptimizationLevel.ORT_ENABLE_ALL = "ALL"
    monkeypatch.setitem(sys.modules, "onnxruntime", fake_ort)
    model = tmp_path / "model.onnx"
    model.write_bytes(b"fake")
    te.ToneEngine(
        str(model),
        intra_threads=2,
        inter_threads=1,
        graph_opt="all",
        disable_arena=True,
        disable_mem_pattern=True,
    )
    _, kwargs = fake_ort.InferenceSession.call_args
    opts = kwargs["sess_options"]
    assert opts.intra_op_num_threads == 2
    assert opts.inter_op_num_threads == 1
    assert opts.graph_optimization_level == "ALL"
    assert opts.enable_cpu_mem_arena is False
    assert opts.enable_mem_pattern is False
    with pytest.raises(ValueError, match="TONE_GRAPH_OPT"):
        te.ToneEngine(str(model), graph_opt="turbo")
