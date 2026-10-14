#!/usr/bin/env python3
"""Задача 2 ночного прогона: перемер ASR-шума с новыми эталонами (ADR-021/022).

Пересчитывает ТОЛЬКО секции asr_rules_B / asr_qwen_B в reports/types_eval.json:
TTS-аудио B (кэш tmp/type_suite/audio_b/) -> whisper-small (тот же код, что
scripts/eval_types.py::asr_transcript) -> оба адаптера -> тот же скоринг.
Остальные 4 секции не трогаются (merge это проверяет assert'ом).

Фазы (Qwen живёт только в backend-сети, поэтому analyze — в api-контейнере):
  1. хост:      python scripts/eval_types_asr.py transcribe --out reports/asr_hyps_B_2026-10-14.json
  2. контейнер: docker cp ... apc_small-api-1:/tmp/; docker exec ... python /app/scripts/eval_types_asr.py analyze
  3. хост:      python scripts/eval_types_asr.py merge (только asr_*-секции)

transcribe и analyze пишут результат после каждого файла (чекпоинт):
повтор с тем же --out продолжает с места обрыва.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.getcwd() or ".")

from scripts.eval_types import (  # noqa: E402
    analyze_llm,
    analyze_rules,
    asr_transcript,
    load_sets,
    score_entities,
    score_types,
    tts_audio,
)


def cmd_transcribe(out: str) -> None:
    _, set_b = load_sets()
    hyps: dict = {}
    if os.path.exists(out):
        hyps = json.load(open(out, encoding="utf-8"))
        print(f"resume: {len(hyps)}/25 гипотез уже есть", flush=True)
    audio_dir = os.path.join("tmp", "type_suite", "audio_b")
    os.makedirs(audio_dir, exist_ok=True)
    for item in set_b:
        if item["id"] in hyps:
            continue
        wav = os.path.join(audio_dir, f"{item['id']}.wav")
        if not os.path.exists(wav):
            tts_audio(item["text"], wav)
        hyps[item["id"]] = asr_transcript(wav)
        json.dump(hyps, open(out, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"{item['id']}: {hyps[item['id']][:60]}", flush=True)
    print(f"done: {len(hyps)} гипотез -> {out}", flush=True)


def cmd_analyze(hyps_path: str, set_b_path: str, out: str) -> None:
    hyps = json.load(open(hyps_path, encoding="utf-8"))
    set_b = json.load(open(set_b_path, encoding="utf-8"))
    partial: dict = {}
    if os.path.exists(out):
        partial = json.load(open(out, encoding="utf-8"))
        print(f"resume: {len(partial)}/25 разборов уже есть", flush=True)
    for item in set_b:
        iid = item["id"]
        if iid in partial:
            continue
        hyp = hyps[iid]
        res_r = analyze_rules(hyp)
        try:
            res_q = analyze_llm(hyp)
        except Exception as e:  # noqa: BLE001
            print(f"llm fallback on {iid}: {type(e).__name__}", flush=True)
            res_q = {"inquiry_type": "other", "entities": {}}
        partial[iid] = {
            "expected_type": item["type"],
            "expected_entities": item["entities"],
            "rules_type": res_r.get("inquiry_type"),
            "rules_entities": res_r.get("entities", {}) or {},
            "qwen_type": res_q.get("inquiry_type"),
            "qwen_entities": res_q.get("entities", {}) or {},
        }
        json.dump(partial, open(out, "w", encoding="utf-8"), ensure_ascii=False)
        print(
            f"{iid}: exp={item['type']} rules={partial[iid]['rules_type']} "
            f"qwen={partial[iid]['qwen_type']}",
            flush=True,
        )
    print(f"done: {len(partial)} разборов -> {out}", flush=True)


def cmd_merge(partial_path: str, report_path: str = "reports/types_eval.json") -> None:
    partial = json.load(open(partial_path, encoding="utf-8"))
    report = json.load(open(report_path, encoding="utf-8"))
    before = {k: v for k, v in report.items() if not k.startswith("asr_")}
    rows_r = [
        {"expected": v["expected_type"], "pred": v["rules_type"]} for v in partial.values()
    ]
    pairs_r = [
        (v["expected_entities"], v["rules_entities"]) for v in partial.values()
    ]
    rows_q = [
        {"expected": v["expected_type"], "pred": v["qwen_type"]} for v in partial.values()
    ]
    pairs_q = [
        (v["expected_entities"], v["qwen_entities"]) for v in partial.values()
    ]
    report["asr_rules_B"] = {
        "types": score_types(rows_r),
        "entities": score_entities(pairs_r),
    }
    report["asr_qwen_B"] = {
        "types": score_types(rows_q),
        "entities": score_entities(pairs_q),
    }
    after = {k: v for k, v in report.items() if not k.startswith("asr_")}
    assert before == after, "не-asr секции изменились — merge остановлен"
    json.dump(report, open(report_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("asr_rules_B", report["asr_rules_B"]["types"], flush=True)
    print("asr_qwen_B", report["asr_qwen_B"]["types"], flush=True)
    print(f"merged -> {report_path} (остальные секции без изменений)", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("transcribe")
    p1.add_argument("--out", default="reports/asr_hyps_B_2026-10-14.json")
    p2 = sub.add_parser("analyze")
    p2.add_argument("--hyps", required=True)
    p2.add_argument("--set-b", required=True)
    p2.add_argument("--out", required=True)
    p3 = sub.add_parser("merge")
    p3.add_argument("--partial", required=True)
    p3.add_argument("--report", default="reports/types_eval.json")
    args = ap.parse_args()
    if args.cmd == "transcribe":
        cmd_transcribe(args.out)
    elif args.cmd == "analyze":
        cmd_analyze(args.hyps, args.set_b, args.out)
    else:
        cmd_merge(args.partial, args.report)


if __name__ == "__main__":
    main()
