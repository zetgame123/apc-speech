import io
import struct
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np

from app.asr.gigaam_engine import (
    GigaamEngine,
    _load_audio_16k_mono,
    _split_long_waveform,
    _tokens_to_words,
)
from app.llm.local_adapter import _llm_extra_kwargs, _strip_think_tags


def _make_wav(duration_sec: float = 1.0, sr: int = 16000) -> bytes:
    n = int(sr * duration_sec)
    t = np.linspace(0.0, duration_sec, n, endpoint=False)
    samples = (np.sin(2 * np.pi * 440.0 * t) * 5000).astype(np.int16)
    data = samples.tobytes()
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(data),
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        sr,
        sr * 2,
        2,
        16,
        b"data",
        len(data),
    )
    return header + data


def test_load_audio_16k_mono_fast_wav_and_resample():
    wav16 = _make_wav(1.0, sr=16000)
    arr16, dur16 = _load_audio_16k_mono(wav16)
    assert arr16.dtype == np.float32
    assert abs(dur16 - 1.0) < 0.01

    wav8 = _make_wav(1.0, sr=8000)
    arr8, dur8 = _load_audio_16k_mono(io.BytesIO(wav8))
    assert len(arr8) == 16000
    assert abs(dur8 - 1.0) < 0.01


def test_split_long_waveform_caps_chunk_duration():
    sr = 16000
    # 50 seconds with a 1-second silence gap at 20s and 40s
    wav = np.zeros(50 * sr, dtype=np.float32)
    wav[0 : 19 * sr] = 0.2
    wav[20 * sr : 39 * sr] = 0.2
    wav[40 * sr : 49 * sr] = 0.2

    chunks = _split_long_waveform(wav, sr=sr, max_sec=22.0)
    assert len(chunks) >= 3
    for start, end, chunk_arr in chunks:
        assert end - start <= 22.5
        assert len(chunk_arr) > 0


def test_tokens_to_words_reconstructs_sentencepiece_words():
    tokens = ["▁щи", "ты", "▁три", "▁на", "▁шесть"]
    timestamps = [0.1, 0.2, 0.5, 0.7, 0.9]
    logprobs = [-0.05, -0.05, -0.1, -0.05, -0.05]

    words, conf = _tokens_to_words(
        tokens, timestamps, logprobs, offset_sec=10.0, chunk_end_sec=12.0
    )
    assert [w["word"] for w in words] == ["щиты", "три", "на", "шесть"]
    assert words[0]["start"] == 10.1
    assert words[-1]["start"] == 10.9
    assert 0.8 <= conf <= 1.0


def test_gigaam_engine_transcribe_normalizes_and_formats_segments():
    engine = GigaamEngine(model_dir=".", quantization="int8", threads=1)
    fake_model = MagicMock()
    fake_model.recognize.return_value = SimpleNamespace(
        text="Нужны щиты три на шесть в Балашихе.",
        tokens=["▁Нужны", "▁щиты", "▁три", "▁на", "▁шесть", "▁в", "▁Балашихе."],
        timestamps=[0.1, 0.4, 0.7, 0.9, 1.1, 1.4, 1.6],
        logprobs=[-0.05] * 7,
    )
    engine._model = fake_model

    segments, meta = engine.transcribe(_make_wav(2.0), language="ru", word_timestamps=True)
    assert len(segments) == 1
    assert "3×6" in segments[0]["text"]
    assert "Балашихе" in segments[0]["text"]
    assert len(segments[0]["words"]) == 7
    assert meta["language"] == "ru"
    assert abs(meta["duration"] - 2.0) < 0.05


def test_qwen3_non_thinking_kwargs_and_tag_stripping():
    assert _llm_extra_kwargs("t-lite-2.1") == {"chat_template_kwargs": {"enable_thinking": False}}
    assert _llm_extra_kwargs("qwen3-4b") == {"chat_template_kwargs": {"enable_thinking": False}}
    assert _llm_extra_kwargs("qwen2.5-7b-instruct") == {}

    raw = '<think>\nreasoning here\n</think>\n{"inquiry_type": "pricing"}'
    assert _strip_think_tags(raw) == '{"inquiry_type": "pricing"}'


def test_apply_vocabulary_hints():
    from app.asr.normalizer import get_normalizer

    norm = get_normalizer()
    hints = "суперсайт, ситиборд, призматрон, медиафасад, Балашиха"
    text = "Нужен суперсайк и презматрон в Балашихе, также ситиборт."
    fixed = norm.apply_vocabulary_hints(text, hints)
    assert "суперсайт" in fixed
    assert "призматрон" in fixed
    assert "ситиборд" in fixed
    # Unrelated words must remain untouched
    assert "Нужен" in fixed and "также" in fixed
