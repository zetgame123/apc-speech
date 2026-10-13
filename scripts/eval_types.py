#!/usr/bin/env python3
"""C10-5: типы и сущности — rules на всех, Qwen на подвыборке, ASR-шум на B.

Метрики: accuracy + macro-F1 типа; precision/recall/F1 сущностей.
Qwen идёт в llm-runtime (local-full). Аудио B — TTS (в tmp/, не в git).
"""

import argparse
import json
import os


def load_sets():
    base = os.path.join("tmp", "type_suite")
    with open(os.path.join(base, "set_a.json"), encoding="utf-8") as f:
        set_a = json.load(f)
    with open(os.path.join(base, "set_b.json"), encoding="utf-8") as f:
        set_b = json.load(f)
    return set_a, set_b


def norm_val(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    return str(v).strip().lower().replace("ё", "е")


def score_types(items: list[dict]) -> dict:
    from collections import Counter

    labels = sorted({x["expected"] for x in items} | {x["pred"] for x in items})
    correct = sum(1 for x in items if x["expected"] == x["pred"])
    f1s = []
    for lab in labels:
        tp = sum(1 for x in items if x["expected"] == lab and x["pred"] == lab)
        fp = sum(1 for x in items if x["expected"] != lab and x["pred"] == lab)
        fn = sum(1 for x in items if x["expected"] == lab and x["pred"] != lab)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * prec * rec / (prec + rec) if prec + rec else 0.0)
    return {
        "n": len(items),
        "accuracy": round(correct / len(items), 4) if items else 0.0,
        "macro_f1": round(sum(f1s) / len(f1s), 4) if f1s else 0.0,
        "confusion": {
            f"{exp}>{pred}": cnt
            for (exp, pred), cnt in Counter(
                (x["expected"], x["pred"]) for x in items if x["expected"] != x["pred"]
            ).items()
        },
    }


PRED_KEY = {
    "city": "cities",
    "format": "formats",
    "quantity": "quantity",
    "period": "target_period",
    "budget": "budget_rub",
    "category": "product_category",
}


def _num(value):
    import re

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    digits = re.sub("[^0-9]", "", str(value))
    return int(digits) if digits else None


def _ent_match(field: str, exp, pred_list_or_val) -> bool | None:
    """True/False/None(оба пусто): сравнение с учётом списков и чисел."""
    if isinstance(pred_list_or_val, list):
        preds = [norm_val(x) for x in pred_list_or_val]
        e = norm_val(exp)
        if e is None:
            return False if preds else None
        return any(e == p or (e and p and (e in p or p in e)) for p in preds)
    e, g = norm_val(exp), norm_val(pred_list_or_val)
    if e is None and g is None:
        return None
    if field in ("quantity", "budget"):
        en, gn = _num(exp), _num(pred_list_or_val)
        if en is None and gn is None:
            return None
        return en == gn
    if e is None or g is None:
        return False
    return e == g or e in g or g in e


def score_entities(items: list[tuple[dict, dict]]) -> dict:
    fields = ["city", "format", "quantity", "period", "budget", "category"]
    out = {}
    for fld in fields:
        tp = fp = fn = 0
        for exp, got in items:
            m = _ent_match(fld, exp.get(fld), (got or {}).get(PRED_KEY[fld]))
            if m is None:
                continue
            if m:
                tp += 1
            else:
                fp += 1
                fn += 1
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        out[fld] = {
            "p": round(prec, 3),
            "r": round(rec, 3),
            "f1": round(2 * prec * rec / (prec + rec), 3) if prec + rec else 0.0,
        }
    return out


def analyze_rules(text: str) -> dict:
    from app.llm.local_adapter import LocalLlamaCppAdapter
    from app.llm.port import AnalysisContext

    adapter = LocalLlamaCppAdapter()
    ctx = AnalysisContext(namespace="eval", call_id="eval", speech_duration_sec=60.0)
    return adapter._analyze_via_rules(text, ctx).model_dump()


def analyze_llm(text: str) -> dict:
    import asyncio as _a

    from app.llm.local_adapter import LocalLlamaCppAdapter
    from app.llm.port import AnalysisContext

    adapter = LocalLlamaCppAdapter()
    ctx = AnalysisContext(namespace="eval", call_id="eval", speech_duration_sec=60.0)
    res = _a.run(adapter._analyze_via_llm(text, ctx))
    return res.model_dump()


def tts_audio(text: str, path: str) -> None:
    import subprocess

    from gtts import gTTS

    mp3 = path.replace(".wav", ".mp3")
    gTTS(text.replace("Клиент:", ".").replace("Менеджер:", "."), lang="ru").save(mp3)
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", mp3, "-ar", "16000", "-ac", "1", path],
        capture_output=True,
        check=True,
    )
    os.remove(mp3)


def asr_transcript(wav_path: str) -> str:
    import io

    from app.asr.engine import AsrEngine

    eng = AsrEngine(model_name="./models/whisper-small")
    with open(wav_path, "rb") as f:
        segs, _ = eng.transcribe(
            io.BytesIO(f.read()),
            language="ru",
            beam_size=1,
            temperature=0.0,
            vad_filter=True,
            word_timestamps=False,
        )
    return " ".join(s["text"] for s in segs)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--qwen-subset", type=int, default=30)
    parser.add_argument("--skip-llm", action="store_true")
    parser.add_argument("--skip-audio", action="store_true")
    args = parser.parse_args()

    set_a, set_b = load_sets()
    report: dict = {}

    # Rules: все 145
    for name, items in (("rules_A", set_a), ("rules_B", set_b)):
        rows, pairs = [], []
        for item in items:
            res = analyze_rules(item["text"])
            rows.append({"expected": item["type"], "pred": res.get("inquiry_type")})
            pairs.append((item["entities"], res.get("entities", {}) or {}))
        report[name] = {"types": score_types(rows), "entities": score_entities(pairs)}
        print(name, report[name]["types"], flush=True)

    if not args.skip_llm:
        # Qwen: стратифицированная подвыборка A (первые 3 каждого типа = 36 -> режем до N)
        # + весь B. Для детерминированности: каждый 4-й из A.
        sub_a = set_a[::4][: args.qwen_subset]
        for name, items in (("qwen_Asub", sub_a), ("qwen_B", set_b)):
            rows, pairs = [], []
            for item in items:
                try:
                    res = analyze_llm(item["text"])
                except Exception as e:
                    res = {"inquiry_type": "other", "entities": {}}
                    print(f"llm fallback on {item['id']}: {type(e).__name__}", flush=True)
                rows.append({"expected": item["type"], "pred": res.get("inquiry_type")})
                pairs.append((item["entities"], res.get("entities", {}) or {}))
            report[name] = {"types": score_types(rows), "entities": score_entities(pairs)}
            print(name, report[name]["types"], flush=True)

    if not args.skip_audio:
        # ASR-шум: TTS аудио B -> whisper-транскрипт -> оба адаптера.
        os.makedirs(os.path.join("tmp", "type_suite", "audio_b"), exist_ok=True)
        for name, fn in (("asr_rules_B", analyze_rules), ("asr_qwen_B", analyze_llm)):
            rows, pairs = [], []
            for item in set_b:
                wav = os.path.join("tmp", "type_suite", "audio_b", f"{item['id']}.wav")
                if not os.path.exists(wav):
                    tts_audio(item["text"], wav)
                hyp = asr_transcript(wav)
                try:
                    res = fn(hyp)
                except Exception:
                    res = {"inquiry_type": "other", "entities": {}}
                rows.append({"expected": item["type"], "pred": res.get("inquiry_type")})
                pairs.append((item["entities"], res.get("entities", {}) or {}))
            report[name] = {"types": score_types(rows), "entities": score_entities(pairs)}
            print(name, report[name]["types"], flush=True)

    with open("reports/types_eval.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
