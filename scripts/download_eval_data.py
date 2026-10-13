#!/usr/bin/env python3
"""Загрузка открытых подмножеств для WER-стенда (C10-2). Только для оценки.

- Common Voice ru (CC0): mozilla-foundation/common_voice_17_0, ~300 фраз.
- Golos (кастомная CC BY-SA-derived, только метрики): saluteeden/golos
  (gated — нужен HF_TOKEN и принятые условия; без них пропускается).
- Телефонные версии: 8 кГц + bandpass + G.711 A-law через ffmpeg.
Аудио и тексты в git НЕ коммитятся (вывод в data/eval_external/, см. .gitignore).
"""

import argparse
import json
import os
import subprocess

OUT_DIR = os.path.join("data", "eval_external")


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, capture_output=True, check=True)


def fetch_cv_ru(n: int, out_dir: str) -> list[dict]:
    """Common Voice ru, validated, первые n по длительности 2-15 с."""
    from datasets import Audio, load_dataset

    try:
        ds = load_dataset(
            "mozilla-foundation/common_voice_17_0", "ru", split="validated", streaming=True
        )
        next(iter(ds))
    except Exception as e:
        print(f"Common Voice: недоступен ({type(e).__name__}) — пропускаю")
        return []
    ds = load_dataset(
        "mozilla-foundation/common_voice_17_0", "ru", split="validated", streaming=True
    )
    ds = ds.cast_column("audio", Audio(sampling_rate=16000, mono=True))
    items = []
    for row in ds:
        text = (row.get("sentence") or "").strip()
        if not text or len(text.split()) < 3:
            continue
        dur = len(row["audio"]["array"]) / row["audio"]["sampling_rate"]
        if not 2.0 <= dur <= 15.0:
            continue
        items.append({"text": text, "array": row["audio"]["array"], "sr": 16000})
        if len(items) >= n:
            break
    return _materialize(items, os.path.join(out_dir, "common_voice_ru"), prefix="cv")


def fetch_golos(n: int, out_dir: str) -> list[dict]:
    """Golos farfield test через parquet-зеркало bond005 (без gated-доступа).

    saluteeden/golos gated (401 без HF_TOKEN и принятых условий) — скрипт
    предпочитает открытое зеркало. Лицензия та же (см. ADR-017).
    """
    import pyarrow.parquet as pq
    from huggingface_hub import hf_hub_download

    try:
        pq_path = hf_hub_download(
            "bond005/sberdevices_golos_100h_farfield",
            "data/test-00000-of-00001-7fb58d4cda8ff9fe.parquet",
            repo_type="dataset",
        )
    except Exception as e:
        print(f"Golos: недоступен ({e}) — пропускаю")
        return []
    table = pq.read_table(pq_path)
    rows = []
    for i in range(table.num_rows):
        text = (table.column("transcription")[i].as_py() or "").strip()
        if len(text.split()) < 4:
            continue
        audio_cell = table.column("audio")[i].as_py()
        blob = audio_cell.get("bytes") if isinstance(audio_cell, dict) else None
        if not blob:
            continue
        rows.append({"text": text, "blob": bytes(blob)})
        if len(rows) >= n:
            break
    return _materialize_blobs(rows, os.path.join(out_dir, "golos"), prefix="golos")


def _materialize_blobs(items: list[dict], split_dir: str, prefix: str) -> list[dict]:
    """Декодирует аудио-блобы через ffmpeg (без torchcodec): clean 16k + телефония."""
    import soundfile as sf

    os.makedirs(split_dir, exist_ok=True)
    tmp_in = os.path.join(split_dir, "_in.tmp")
    manifest = []
    for i, item in enumerate(items):
        name = f"{prefix}_{i:03d}"
        clean_path = os.path.join(split_dir, f"{name}_16k.wav")
        tel_path = os.path.join(split_dir, f"{name}_8k.wav")
        with open(tmp_in, "wb") as f:
            f.write(item["blob"])
        run(
            [
                "ffmpeg",
                "-nostdin",
                "-y",
                "-v",
                "error",
                "-i",
                tmp_in,
                "-ar",
                "16000",
                "-ac",
                "1",
                clean_path,
            ]
        )
        run(
            [
                "ffmpeg",
                "-nostdin",
                "-y",
                "-v",
                "error",
                "-i",
                clean_path,
                "-af",
                "highpass=f=300,lowpass=f=3400",
                "-ar",
                "8000",
                "-ac",
                "1",
                "-c:a",
                "pcm_alaw",
                tel_path,
            ]
        )
        info = sf.info(clean_path)
        if not 2.0 <= info.duration <= 15.0:
            os.remove(clean_path)
            os.remove(tel_path)
            continue
        manifest.append(
            {
                "id": name,
                "reference": item["text"],
                "clean": clean_path,
                "telephony": tel_path,
                "duration_sec": round(info.duration, 2),
            }
        )
    if os.path.exists(tmp_in):
        os.remove(tmp_in)
    with open(os.path.join(split_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"{split_dir}: {len(manifest)} фраз")
    return manifest


def _materialize(items: list[dict], split_dir: str, prefix: str) -> list[dict]:
    import numpy as np
    import soundfile as sf

    os.makedirs(split_dir, exist_ok=True)
    manifest = []
    for i, item in enumerate(items):
        name = f"{prefix}_{i:03d}"
        clean_path = os.path.join(split_dir, f"{name}_16k.wav")
        tel_path = os.path.join(split_dir, f"{name}_8k.wav")
        sf.write(clean_path, np.asarray(item["array"], dtype=np.float32), 16000)
        run(
            [
                "ffmpeg",
                "-nostdin",
                "-y",
                "-v",
                "error",
                "-i",
                clean_path,
                "-af",
                "highpass=f=300,lowpass=f=3400",
                "-ar",
                "8000",
                "-ac",
                "1",
                "-c:a",
                "pcm_alaw",
                tel_path,
            ]
        )
        info = sf.info(clean_path)
        manifest.append(
            {
                "id": name,
                "reference": item["text"],
                "clean": clean_path,
                "telephony": tel_path,
                "duration_sec": round(info.duration, 2),
            }
        )
    with open(os.path.join(split_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"{split_dir}: {len(manifest)} фраз")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=300)
    parser.add_argument("--no-golos", action="store_true")
    args = parser.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)
    fetch_cv_ru(args.n, OUT_DIR)
    if not args.no_golos:
        fetch_golos(args.n, OUT_DIR)


if __name__ == "__main__":
    main()
