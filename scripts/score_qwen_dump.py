#!/usr/bin/env python3
"""Сборка reports/types_eval.json из частей (детерминировано, без LLM).

- rules_A/rules_B: прямой прогон правил на наборах A/B (быстро, хост);
- qwen_Asub/qwen_B: подсчёт по дампу Qwen (reports/qwen_dump_*.json);
- asr_*: берутся из git HEAD (ASR-шум не перемеряется в п.1-2).

Использование:
  uv run python scripts/score_qwen_dump.py reports/qwen_dump_v14.json
"""

import json
import subprocess
import sys


def main(dump_path: str) -> None:
    from scripts.eval_types import analyze_rules, load_sets, score_entities, score_types

    set_a, set_b = load_sets()
    report: dict = {}

    for name, items in (("rules_A", set_a), ("rules_B", set_b)):
        rows, pairs = [], []
        for item in items:
            res = analyze_rules(item["text"])
            rows.append({"expected": item["type"], "pred": res.get("inquiry_type")})
            pairs.append((item["entities"], res.get("entities", {}) or {}))
        report[name] = {"types": score_types(rows), "entities": score_entities(pairs)}
        print(name, report[name]["types"], flush=True)

    dump = json.load(open(dump_path, encoding="utf-8"))
    for name in ("qwen_Asub", "qwen_B"):
        rows = [{"expected": r["expected_type"], "pred": r["pred_type"]} for r in dump[name]]
        pairs = [(r["expected_entities"], r["pred_entities"] or {}) for r in dump[name]]
        report[name] = {"types": score_types(rows), "entities": score_entities(pairs)}
        print(name, report[name]["types"], flush=True)

    old = json.loads(
        subprocess.run(
            ["git", "show", "HEAD:reports/types_eval.json"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout
    )
    for name in ("asr_rules_B", "asr_qwen_B"):
        report[name] = old[name]
        print(name, "kept from HEAD", report[name]["types"], flush=True)

    with open("reports/types_eval.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main(sys.argv[1])
