#!/usr/bin/env python3
"""П.2: рычаги скорости ASR длинного пути и их цена по WER.

Движок/параметры — как в проде worker-long (podlodka-turbo-ct2, int8, CPU,
ASR_THREADS=2, temperature=0.0, conversation-промпт по умолчанию,
hotwords=None), меняется по одному фактору за раз:
  A_base    : beam=2, VAD-дефолт, word_timestamps=True (прод)
  B_beam1   : beam=1
  F_vadfast : быстрый VAD (min_silence 250 мс, pad 200 мс)
  W_nowords : word_timestamps=False (цена — потеря словых таймкодов C2)
  G_novad   : vad_filter=False (референс; в прод длинных нельзя — нет сегментации)

Замер: первые N манифеста (детерминировано) x clean/telephone, пофайлово
(WER корпусный + RTF), плюс склейка ~100 с одним вызовом vs 3 чанка
(цена размера чанков). int8 не варьируется: это уже скоростной оптимум CPU
(альтернативы float32/float16 только медленнее и тяжелее; compose пинит
int8 для worker-long) — менять нечего, измерять нечего.
VAD-дефолт: threshold=0.35, min_silence=450 мс, pad=300 мс.

Запуск: taskset -c 4,6 ASR_THREADS=2 python3 scripts/bench_asr_levers.py
Частоту не менять (кап пользователя 3,0 ГГц). Выход: reports/asr_levers.json.
Цифры только из этого запуска.
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

from app.asr.engine import AsrEngine
from evaluation.wer_cer import levenshtein_distance, normalize_eval_text

BASE_VAD = {"threshold": 0.35, "min_silence_duration_ms": 450, "speech_pad_ms": 300}
FAST_VAD = {"threshold": 0.35, "min_silence_duration_ms": 250, "speech_pad_ms": 200}

CONFIGS = {
    "A_base": {"beam_size": 2, "vad_filter": True, "word_timestamps": True},
    "B_beam1": {"beam_size": 1, "vad_filter": True, "word_timestamps": True},
    "F_vadfast": {
        "beam_size": 2,
        "vad_filter": True,
        "word_timestamps": True,
        "vad_parameters": FAST_VAD,
    },
    "W_nowords": {"beam_size": 2, "vad_filter": True, "word_timestamps": False},
    "G_novad": {"beam_size": 2, "vad_filter": False, "word_timestamps": True},
}


def _transcribe(eng: AsrEngine, audio_bytes: bytes, cfg: dict) -> str:
    kw: dict = {
        "language": "ru",
        "beam_size": cfg["beam_size"],
        "temperature": 0.0,
        "vad_filter": cfg["vad_filter"],
        "word_timestamps": cfg["word_timestamps"],
        "initial_prompt": None,  # дефолт движка = conversation-промпт (как в проде без hints)
        "hotwords": None,
    }
    if "vad_parameters" in cfg:
        kw["vad_parameters"] = dict(cfg["vad_parameters"])
    segs, _ = eng.transcribe(io.BytesIO(audio_bytes), **kw)
    return " ".join(s["text"] for s in segs)


def _machine_info() -> dict:
    info: dict = {"asr_threads": os.environ.get("ASR_THREADS", "?")}
    try:
        info["affinity"] = sorted(os.sched_affinity(0))
    except Exception:
        pass
    try:
        freqs = {}
        for cpu in (4, 6):
            with open(f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/scaling_cur_freq") as f:
                freqs[f"cpu{cpu}_mhz"] = round(int(f.read().strip()) / 1000, 1)
        with open("/sys/devices/system/cpu/cpu4/cpufreq/scaling_max_freq") as f:
            info["cap_khz"] = int(f.read().strip())
        info.update(freqs)
    except Exception as e:
        info["freq_err"] = str(e)
    return info


def _concat(items: list[dict], cond: str, tag: str) -> tuple[str, str]:
    import hashlib

    key = hashlib.md5(("".join(i["id"] for i in items) + cond + tag).encode()).hexdigest()[:8]
    out = f"/tmp/opencode/levers_concat_{tag}_{cond}_{key}.wav"
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=25)
    ap.add_argument(
        "--datasets",
        default="golos:clean:telephony,ruls:clean:telephony,sova:clean:telephony",
        help="список имя:манифест-ключ...",
    )
    ap.add_argument("--configs", default="A_base,B_beam1,F_vadfast,W_nowords,G_novad")
    ap.add_argument("--out", default="reports/asr_levers.json")
    ap.add_argument("--manifest-dir", default="data/eval_external")
    args = ap.parse_args()

    manifests = {
        "golos": os.path.join(args.manifest_dir, "golos", "manifest.json"),
        "ruls": os.path.join(args.manifest_dir, "russian_librispeech", "manifest.json"),
        "sova": os.path.join(args.manifest_dir, "sova_rudevices", "manifest.json"),
    }

    eng = AsrEngine(model_name="./models/whisper-podlodka-turbo-ct2")
    eng.warmup()
    report: dict = {"n": args.n, "machine": _machine_info(), "datasets": {}}

    for ds in args.datasets.split(","):
        name = ds.split(":")[0]
        with open(manifests[name], encoding="utf-8") as f:
            items = json.load(f)[: args.n]
        ds_rep: dict = {}
        for cfg_name in args.configs.split(","):
            cfg_name = cfg_name.strip()
            cfg = CONFIGS[cfg_name]
            row: dict = {}
            for cond in ("clean", "telephony"):
                err_w = ref_w = wall = dur = 0.0
                for item in items:
                    raw = open(item[cond], "rb").read()
                    t0 = time.perf_counter()
                    hyp = _transcribe(eng, raw, cfg)
                    wall += time.perf_counter() - t0
                    dur += item.get("duration_sec") or sf.info(item[cond]).duration
                    rn, hn = normalize_eval_text(item["reference"]), normalize_eval_text(hyp)
                    rw, hw = rn.split(), hn.split()
                    err_w += levenshtein_distance(rw, hw)
                    ref_w += len(rw)
                row[cond] = {
                    "wer": round(err_w / ref_w, 4) if ref_w else 0.0,
                    "rtf": round(wall / dur, 3) if dur else 0.0,
                    "wall": round(wall, 1),
                    "dur": round(dur, 1),
                }
            ds_rep[cfg_name] = row
            print(f"{name}/{cfg_name}: {row}", flush=True)
        # Чанки: склейка одним вызовом vs 3 куска (тот же A_base).
        chunk_row: dict = {}
        for cond in ("clean", "telephony"):
            wav, ref = _concat(items, cond, f"{name}{args.n}")
            raw = open(wav, "rb").read()
            t0 = time.perf_counter()
            hyp1 = _transcribe(eng, raw, CONFIGS["A_base"])
            wall1 = time.perf_counter() - t0
            dur1 = sf.info(wav).duration
            k = len(items) // 3
            parts = [items[:k], items[k : 2 * k], items[2 * k :]]
            wall3, hyp3, dur3 = 0.0, [], 0.0
            for p in parts:
                w, _ = _concat(p, cond, f"{name}{args.n}p{len(hyp3)}")
                r = open(w, "rb").read()
                t0 = time.perf_counter()
                hyp3.append(_transcribe(eng, r, CONFIGS["A_base"]))
                wall3 += time.perf_counter() - t0
                dur3 += sf.info(w).duration
            rn = normalize_eval_text(ref)
            w1 = levenshtein_distance(rn.split(), normalize_eval_text(hyp1).split())
            w3 = levenshtein_distance(rn.split(), normalize_eval_text(" ".join(hyp3)).split())
            nwords = len(rn.split())
            chunk_row[cond] = {
                "x1": {"wer": round(w1 / nwords, 4), "rtf": round(wall1 / dur1, 3)},
                "x3": {"wer": round(w3 / nwords, 4), "rtf": round(wall3 / dur3, 3)},
                "dur": round(dur1, 1),
            }
        ds_rep["chunks_A"] = chunk_row
        print(f"{name}/chunks_A: {chunk_row}", flush=True)
        report["datasets"][name] = ds_rep
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"machine={report['machine']}", flush=True)


if __name__ == "__main__":
    main()
