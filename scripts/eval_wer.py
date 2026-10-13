#!/usr/bin/env python3
"""WER/CER-стенд C10-3: движки x условия (чистая речь / телефония).

Движки на уровне движка (без HTTP): whisper-podlodka (длинный путь),
whisper-small, T-one. Выборка: первые N записей манифеста (детерминировано).
Аудио и тексты в git не входят. Выход: reports/wer_*.json
"""

import argparse
import io
import json
import os
import time

from evaluation.wer_cer import calculate_wer_cer


def load_manifest(path: str, n: int) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        manifest = json.load(f)
    return manifest[:n]


def transcribe_podlodka_or_small(model_path: str, wav_path: str) -> str:
    from app.asr.engine import AsrEngine

    eng = AsrEngine(model_name=model_path)
    with open(wav_path, "rb") as f:
        segs, _ = eng.transcribe(
            io.BytesIO(f.read()),
            language="ru",
            beam_size=2,
            temperature=0.0,
            vad_filter=True,
            word_timestamps=False,
        )
    return " ".join(s["text"] for s in segs)


def transcribe_tone(wav_path: str) -> str:
    import soundfile as sf

    from app.asr.tone_engine import get_tone_engine

    data, sr = sf.read(wav_path, dtype="int16")
    with open(wav_path, "rb") as f:
        raw = f.read()
    # G.711/alaw и прочие PCM-варианты — через PCM16 (как препроцессор в проде).
    if raw[20:22] != b"\x01\x00":
        import io
        import wave

        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(data.tobytes())
        raw = buf.getvalue()
    segs, _ = get_tone_engine().transcribe(raw, language="ru")
    return " ".join(s["text"] for s in segs)


ENGINES = {
    "podlodka": lambda p: transcribe_podlodka_or_small("./models/whisper-podlodka-turbo-ct2", p),
    "whisper-small": lambda p: transcribe_podlodka_or_small("./models/whisper-small", p),
    "t-one": transcribe_tone,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="data/eval_external/golos/manifest.json")
    parser.add_argument("--n", type=int, default=100)
    parser.add_argument("--engines", default="podlodka,whisper-small,t-one")
    parser.add_argument("--out", default="reports/wer_golos.json")
    args = parser.parse_args()

    items = load_manifest(args.manifest, args.n)
    report: dict = {"n": len(items), "engines": {}}
    for eng_name in args.engines.split(","):
        fn = ENGINES[eng_name.strip()]
        for cond in ("clean", "telephony"):
            key = "clean" if cond == "clean" else "telephony"
            err_w = ref_w = err_c = ref_c = 0
            per_file = []
            t0 = time.time()
            for item in items:
                hyp = fn(item[key])
                wer, cer = calculate_wer_cer(item["reference"], hyp)
                ref_norm_len = len(item["reference"].split())
                err_w += wer * ref_norm_len
                ref_w += ref_norm_len
                # CER агрегируем взвешенно по длине (приближение, документировано).
                err_c += cer * len(item["reference"])
                ref_c += len(item["reference"])
                per_file.append({"id": item["id"], "wer": wer, "cer": cer})
            dt = time.time() - t0
            report["engines"].setdefault(eng_name, {})[cond] = {
                "wer": round(err_w / ref_w, 4) if ref_w else 0.0,
                "cer": round(err_c / ref_c, 4) if ref_c else 0.0,
                "seconds_total": round(dt, 1),
            }
            print(
                f"{eng_name}/{cond}: WER={report['engines'][eng_name][cond]['wer']} "
                f"CER={report['engines'][eng_name][cond]['cer']} ({dt:.0f}с)",
                flush=True,
            )
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
