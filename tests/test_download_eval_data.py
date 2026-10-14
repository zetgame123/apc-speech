"""Тесты загрузчика оценочных наборов (без сети, синтетический parquet)."""

import io
import json
import os

import numpy as np
import pandas as pd
import soundfile as sf


def _wav_bytes(sec: float = 3.0, sr: int = 16000) -> bytes:
    t = np.linspace(0, sec, int(sr * sec), dtype=np.float32)
    tone = 0.1 * np.sin(2 * np.pi * 440 * t).astype(np.float32)
    buf = io.BytesIO()
    sf.write(buf, tone, sr, format="WAV")
    return buf.getvalue()


def _write_parquet(path: str) -> None:
    blob = _wav_bytes()
    df = pd.DataFrame(
        [
            {
                "audio": {"bytes": blob, "path": "a.wav"},
                "text": "предобработанный вариант",
                "text_no_preprocessing": "Сырая расшифровка из четырёх слов",
            },
            {
                "audio": {"bytes": blob, "path": "b.wav"},
                "text": "предобработанный вариант",
                "text_no_preprocessing": "",
            },
            {"audio": {"bytes": b"", "path": "c.wav"}, "text": "слова без аудио тут", "x": 1},
        ]
    )
    df.to_parquet(path)


def test_rows_from_parquet_prefers_raw_text_and_skips_empty(tmp_path):
    from scripts.download_eval_data import _rows_from_parquet

    pq_path = str(tmp_path / "t.parquet")
    _write_parquet(pq_path)
    rows = _rows_from_parquet(pq_path, ["text_no_preprocessing", "text"])
    assert len(rows) == 1
    assert rows[0]["text"] == "Сырая расшифровка из четырёх слов"
    assert rows[0]["blob"][:4] == b"RIFF"


def test_materialize_blobs_writes_manifest_and_both_conditions(tmp_path):
    from scripts.download_eval_data import _materialize_blobs

    rows = [{"text": "Сырая расшифровка из четырёх слов", "blob": _wav_bytes()}]
    split_dir = str(tmp_path / "split")
    manifest = _materialize_blobs(rows, split_dir, prefix="syn")
    assert len(manifest) == 1
    assert os.path.exists(manifest[0]["clean"])
    assert os.path.exists(manifest[0]["telephony"])
    with open(os.path.join(split_dir, "manifest.json"), encoding="utf-8") as f:
        saved = json.load(f)
    assert saved[0]["reference"] == "Сырая расшифровка из четырёх слов"
