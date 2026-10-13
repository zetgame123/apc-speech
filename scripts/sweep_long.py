#!/usr/bin/env python3
"""Свип дешёвых настроек длинного пути (podlodka) в бюджете RTF ≤ 1.0 на 2 ядрах.

База = продакшн (worker-long): beam=2, initial_prompt=conversation-дефолт,
hotwords=None, VAD-дефолт, word_timestamps=True, вход — байты файла как есть.
Варианты меняют по одному фактору; hotwords/initial_prompt — вкл/выкл
отдельно и по условиям (clean/telephone). Замер на первых N манифеста Golos
(детерминировано) + доля терминов на готовых TTS-20 (tmp/eval_terms, без сети).

Запуск под 2 ядрами: taskset -c 4,6 + ASR_THREADS=2 (см. отчёт).
Выход: reports/long_sweep.json. Цифры только из этого запуска.
"""

import argparse
import io
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import soundfile as sf
import yaml

from app.asr.engine import DEFAULT_HOTWORDS, AsrEngine
from evaluation.wer_cer import levenshtein_distance, normalize_eval_text

BASE_VAD = {"threshold": 0.35, "min_silence_duration_ms": 450, "speech_pad_ms": 300}
FAST_VAD = {"threshold": 0.35, "min_silence_duration_ms": 250, "speech_pad_ms": 200}

CONFIGS = {
    "A_base": {},
    "B_beam1": {"beam_size": 1},
    "C_hotwords": {"hotwords": DEFAULT_HOTWORDS},
    "D_noprompt": {"no_prompt": True},
    "E_hints": {"hotwords": DEFAULT_HOTWORDS, "initial_prompt": "HINTS"},
    "F_vadfast": {"vad_parameters": FAST_VAD},
    "G_novad": {"vad_filter": False},
    "H_loudnorm": {"preprocess": "loudnorm"},
    "I_soxrhq": {"preprocess": "soxrhq"},
}


def _hints_prompt() -> str:
    with open(os.path.join("config", "vocabulary_hints.yaml"), encoding="utf-8") as f:
        data = yaml.safe_load(f)
    terms = [str(t) for v in data.values() if isinstance(v, list) for t in v]
    return " ".join(terms[:40])


def _preprocess(src: str, mode: str, cache: dict) -> bytes:
    """loudnorm-цепочка как в проде; soxrhq — тот же + точный ресемплер soxr."""
    key = (src, mode)
    if key in cache:
        return cache[key]
    if mode == "loudnorm":
        af = "highpass=f=80,loudnorm=I=-16:TP=-1.5:LRA=11"
    else:
        af = (
            "highpass=f=80,aresample=16000:resampler=soxr:precision=28,"
            "loudnorm=I=-16:TP=-1.5:LRA=11"
        )
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-threads",
        "1",
        "-i",
        src,
        "-vn",
        "-af",
        af,
        "-ar",
        "16000",
        "-ac",
        "1",
        "-f",
        "wav",
        "pipe:1",
    ]
    res = subprocess.run(cmd, capture_output=True, check=True, timeout=120)
    cache[key] = res.stdout
    return res.stdout


def _transcribe(eng: AsrEngine, audio_bytes: bytes, cfg: dict, hints: str) -> str:
    kw: dict = {
        "language": "ru",
        "beam_size": cfg.get("beam_size", 2),
        "temperature": 0.0,
        "vad_filter": cfg.get("vad_filter", True),
        "word_timestamps": True,
    }
    if "vad_parameters" in cfg:
        kw["vad_parameters"] = dict(cfg["vad_parameters"])
    prompt = cfg.get("initial_prompt", "DEFAULT")
    if cfg.get("no_prompt"):
        # В обход фолбэка AsrEngine: сырой вызов модели без initial_prompt.
        segs_iter, _ = eng.model.transcribe(
            io.BytesIO(audio_bytes),
            initial_prompt=None,
            hotwords=None,
            condition_on_previous_text=False,
            no_speech_threshold=0.6,
            compression_ratio_threshold=2.4,
            log_prob_threshold=-1.0,
            **kw,
        )
        return " ".join(eng.normalizer.normalize(s.text.strip()) for s in segs_iter)
    kw["initial_prompt"] = hints if prompt == "HINTS" else None
    kw["hotwords"] = cfg.get("hotwords")
    segs, _ = eng.transcribe(io.BytesIO(audio_bytes), **kw)
    return " ".join(s["text"] for s in segs)


def _machine_info() -> dict:
    info: dict = {"asr_threads": os.environ.get("ASR_THREADS", "?")}
    try:
        info["affinity"] = sorted(os.sched_affinity(0))
    except Exception:
        pass
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            mhz = [float(line.split(":")[1]) / 1000 for line in f if line.startswith("cpu MHz")]
        info["ghz_min"] = round(min(mhz), 2)
        info["ghz_max"] = round(max(mhz), 2)
    except Exception:
        pass
    return info


def _terms_set() -> tuple[list[dict], list[str]]:
    with open(os.path.join("config", "vocabulary_hints.yaml"), encoding="utf-8") as f:
        hints = yaml.safe_load(f)
    terms = [str(t) for v in hints.values() if isinstance(v, list) for t in v]
    with open(os.path.join("tmp", "eval_terms", "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    items = []
    for row in manifest:
        wav = os.path.join("tmp", "eval_terms", row["id"] + ".wav")
        if os.path.exists(wav):
            items.append({"id": row["id"], "reference": row["reference"], "wav": wav})
    return items, terms


def _concat_wav(items: list[dict], cond: str, n: int) -> tuple[str, str]:
    """Склейка N файлов в один длинный (~100 с): ближе к чанкам длинного пути."""
    import hashlib

    key = hashlib.md5(("".join(i["id"] for i in items) + cond).encode()).hexdigest()[:8]
    out = f"/tmp/opencode/sweep_concat_{cond}_{n}_{key}.wav"
    ref = " ".join(i["reference"] for i in items)
    if not os.path.exists(out):
        lst = out + ".txt"
        with open(lst, "w", encoding="utf-8") as f:
            for item in items:
                f.write(f"file '{os.path.abspath(item[cond])}'\n")
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-y",
                "-v",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                lst,
                "-ar",
                "16000",
                "-ac",
                "1",
                out,
            ],
            check=True,
            timeout=120,
        )
        os.remove(lst)
    return out, ref


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="data/eval_external/golos/manifest.json")
    parser.add_argument("--n", type=int, default=30)
    parser.add_argument("--configs", default=",".join(CONFIGS))
    parser.add_argument("--out", default="reports/long_sweep.json")
    args = parser.parse_args()

    with open(args.manifest, encoding="utf-8") as f:
        items = json.load(f)[: args.n]
    term_items, terms = _terms_set()
    hints = _hints_prompt()

    eng = AsrEngine(model_name="./models/whisper-podlodka-turbo-ct2")
    eng.warmup()
    cache: dict = {}
    report: dict = {"n": len(items), "machine": _machine_info(), "configs": {}}

    for name in args.configs.split(","):
        name = name.strip()
        cfg = CONFIGS[name]
        row: dict = {}
        for cond in ("clean", "telephony"):
            err_w = ref_w = err_c = ref_c = wall = dur = 0.0
            for item in items:
                raw = open(item[cond], "rb").read()
                if "preprocess" in cfg:
                    raw = _preprocess(item[cond], cfg["preprocess"], cache)
                t0 = time.perf_counter()
                hyp = _transcribe(eng, raw, cfg, hints)
                wall += time.perf_counter() - t0
                dur += item.get("duration_sec") or sf.info(item[cond]).duration
                rn, hn = normalize_eval_text(item["reference"]), normalize_eval_text(hyp)
                rw, hw = rn.split(), hn.split()
                err_w += levenshtein_distance(rw, hw)
                ref_w += len(rw)
                err_c += levenshtein_distance(list(rn), list(hn))
                ref_c += len(rn)
            row[cond] = {
                "wer": round(err_w / ref_w, 4) if ref_w else 0.0,
                "cer": round(err_c / ref_c, 4) if ref_c else 0.0,
                "rtf": round(wall / dur, 3) if dur else 0.0,
            }
        # Доля терминов (clean 16кГц, те же декодерные параметры).
        hit = tot = 0
        for ti in term_items:
            raw = open(ti["wav"], "rb").read()
            if "preprocess" in cfg:
                raw = _preprocess(ti["wav"], cfg["preprocess"], cache)
            hyp = _transcribe(eng, raw, cfg, hints)
            ref_n, hyp_n = normalize_eval_text(ti["reference"]), normalize_eval_text(hyp)
            expected = [
                t for t in terms if normalize_eval_text(t) and normalize_eval_text(t) in ref_n
            ]
            hit += sum(1 for t in expected if normalize_eval_text(t) in hyp_n)
            tot += len(expected)
        row["terms"] = f"{hit}/{tot}={round(hit / tot, 3) if tot else 0}"
        long_wav, long_ref = _concat_wav(items, "clean", len(items))
        raw = open(long_wav, "rb").read()
        if "preprocess" in cfg:
            raw = _preprocess(long_wav, cfg["preprocess"], cache)
        t0 = time.perf_counter()
        long_hyp = _transcribe(eng, raw, cfg, hints)
        long_wall = time.perf_counter() - t0
        long_dur = sf.info(long_wav).duration
        rn, hn = normalize_eval_text(long_ref), normalize_eval_text(long_hyp)
        rw, hw = rn.split(), hn.split()
        d_w = levenshtein_distance(rw, hw)
        row["long100s"] = {
            "wer": round(d_w / len(rw), 4) if rw else 0.0,
            "rtf": round(long_wall / long_dur, 3) if long_dur else 0.0,
            "dur": round(long_dur, 1),
        }
        report["configs"][name] = row
        print(
            f"{name}: clean={row['clean']} tel={row['telephony']} "
            f"long={row['long100s']} terms={row['terms']}",
            flush=True,
        )

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
