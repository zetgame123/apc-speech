#!/usr/bin/env python3
"""WER/CER-стенд C10-3: движки x условия (чистая речь / телефония).

Движки на уровне движка (без HTTP): whisper-podlodka (длинный путь),
whisper-small, T-one. Выборка: первые N записей манифеста (детерминировано).
Аудио и тексты в git не входят. Выход: reports/wer_*.json

Агрегация — корпусная (сумма расстояний / сумма длин НОРМАЛИЗОВАННЫХ
текстов), как в evaluation/run.py. Взвешивание по длинам сырых строк
и усреднение округлённых per-file WER — ошибка, исправлена.
Движки переиспользуются между файлами (раньше модель грузилась заново
на каждый файл).
"""

import argparse
import json
import os
import time

from evaluation.wer_cer import levenshtein_distance, normalize_eval_text


def load_manifest(path: str, n: int) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        manifest = json.load(f)
    return manifest[:n]


def _whisper_fn(eng) -> object:
    import io

    from app.asr.engine import AsrEngine  # noqa: F401  (тип для читаемости)

    def _whisper(wav_path: str) -> str:
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

    return _whisper


def _tone_fn(tone) -> object:
    import io

    def _tone(wav_path: str) -> str:
        import soundfile as sf

        data, sr = sf.read(wav_path, dtype="int16")
        with open(wav_path, "rb") as f:
            raw = f.read()
        if raw[20:22] != b"\x01\x00":
            import wave

            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(sr)
                w.writeframes(data.tobytes())
            raw = buf.getvalue()
        segs, _ = tone.transcribe(raw, language="ru")
        return " ".join(s["text"] for s in segs)

    return _tone


def make_transcribers(only: str = "") -> dict:
    """Ленивые конструкторы движков: модель грузится при первом обращении.

    only — подмножество через запятую (чтобы не грузить whisper-small,
    когда меряются только podlodka/t-one). Нужно для чанков/продолжения:
    недостающие гипотезы достраиваются, имеющиеся берутся из кэша.
    """
    from app.asr.engine import AsrEngine
    from app.asr.tone_engine import get_tone_engine

    wanted = [e.strip() for e in only.split(",")] if only else []
    loaded: dict = {}

    def _get(name: str):
        if name not in loaded:
            if name == "t-one":
                loaded[name] = _tone_fn(get_tone_engine())
            else:
                model = (
                    "./models/whisper-podlodka-turbo-ct2"
                    if name == "podlodka"
                    else "./models/whisper-small"
                )
                loaded[name] = _whisper_fn(AsrEngine(model_name=model))
        return loaded[name]

    names = wanted or ["podlodka", "whisper-small", "t-one"]
    return {name: (lambda p, n=name: _get(n)(p)) for name in names}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="data/eval_external/golos/manifest.json")
    parser.add_argument("--n", type=int, default=100)
    parser.add_argument("--engines", default="podlodka,whisper-small,t-one")
    parser.add_argument("--out", default="reports/wer_golos.json")
    parser.add_argument("--save-hyps", default="")
    parser.add_argument("--hyps-in", default="")
    args = parser.parse_args()

    items = load_manifest(args.manifest, args.n)
    cached: dict = {}
    if args.hyps_in and os.path.exists(args.hyps_in):
        with open(args.hyps_in, encoding="utf-8") as f:
            cached = json.load(f).get("hyps", {})

    transcribers = make_transcribers(only=args.engines)
    hyps: dict = {k: v for k, v in cached.items()}
    report: dict = {"n": len(items), "engines": {}}
    for eng_name in args.engines.split(","):
        eng_name = eng_name.strip()
        fn = transcribers.get(eng_name) if transcribers else None
        for cond in ("clean", "telephony"):
            err_w = ref_w = err_c = ref_c = 0
            per_file = []
            t0 = time.time()
            for item in items:
                ck = f"{eng_name}/{item['id']}/{cond}"
                if ck in hyps:
                    hyp = hyps[ck]
                else:
                    assert fn is not None, f"нет кэша {ck} и нет движка"
                    hyp = fn(item[cond])
                    hyps[ck] = hyp
                    # Чекпоинт: сброс кэша каждые 10 файлов, чтобы обрыв
                    # не терял прогресс (продолжение — тем же --hyps-in).
                    if args.save_hyps and len(hyps) % 10 == 0:
                        with open(args.save_hyps, "w", encoding="utf-8") as f:
                            json.dump({"hyps": hyps}, f, ensure_ascii=False)
                ref_norm = normalize_eval_text(item["reference"])
                hyp_norm = normalize_eval_text(hyp)
                ref_words = ref_norm.split()
                hyp_words = hyp_norm.split()
                d_w = levenshtein_distance(ref_words, hyp_words)
                d_c = levenshtein_distance(list(ref_norm), list(hyp_norm))
                err_w += d_w
                ref_w += len(ref_words)
                err_c += d_c
                ref_c += len(ref_norm)
                wer = d_w / len(ref_words) if ref_words else 0.0
                cer = d_c / len(ref_norm) if ref_norm else 0.0
                per_file.append({"id": item["id"], "wer": round(wer, 4), "cer": round(cer, 4)})
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
    if args.save_hyps:
        with open(args.save_hyps, "w", encoding="utf-8") as f:
            json.dump({"hyps": hyps}, f, ensure_ascii=False)


if __name__ == "__main__":
    main()
