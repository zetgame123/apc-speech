#!/usr/bin/env python3
"""П.4: сущности Qwen на B до/после добивки правилами (без новых вызовов LLM).

- до: сырые выходы повторного прогона (reports/qwen_repeat_b3_*, pass 1,
  temperature=0.0 + seed=42, без merge);
- после: те же выходы + backfill_entities_from_rules (детерминировано).
Эталоны — текущие tmp/type_suite (до п.5). Типы merge не трогает.

Использование:
  uv run python scripts/score_merge.py reports/qwen_repeat_b3_2026-10-13.json
"""

import json
import sys


def main(dump_path: str) -> None:
    from app.llm.local_adapter import LocalLlamaCppAdapter, backfill_entities_from_rules
    from scripts.eval_types import load_sets, score_entities

    dump = json.load(open(dump_path, encoding="utf-8"))
    _, set_b = load_sets()
    by_id = {x["id"]: x for x in set_b}
    adapter = LocalLlamaCppAdapter()

    before_pairs, after_pairs = [], []
    for row in dump["passes"][0]:
        exp = by_id[row["id"]]["entities"]
        raw = row.get("entities", {}) or {}
        merged = backfill_entities_from_rules(
            dict(raw), by_id[row["id"]]["text"], adapter.categories
        )
        before_pairs.append((exp, raw))
        after_pairs.append((exp, merged))

    before = score_entities(before_pairs)
    after = score_entities(after_pairs)
    print("BEFORE", json.dumps(before, ensure_ascii=False))
    print("AFTER ", json.dumps(after, ensure_ascii=False))
    json.dump(
        {"before": before, "after": after},
        open("reports/qwen_merge_b_2026-10-13.json", "w", encoding="utf-8"),
        ensure_ascii=False,
        indent=2,
    )


if __name__ == "__main__":
    main(sys.argv[1])
