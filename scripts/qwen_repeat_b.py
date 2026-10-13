#!/usr/bin/env python3
"""П.3: повтор набора B через Qwen 3 раза, разброс между прогонами.

Использование (внутри api-контейнера, llm-runtime доступен как llm-runtime:8080):
  docker cp tmp/type_suite/set_b.json apc_small-api-1:/tmp/rep_b.json
  docker cp scripts/qwen_repeat_b.py apc_small-api-1:/tmp/qwen_repeat_b.py
  docker exec -e APP_ENV=development -e DEV_TOKEN=dev-token apc_small-api-1 \\
    python /tmp/qwen_repeat_b.py /tmp/rep_b.json /tmp/rep_b_out.json
  docker cp apc_small-api-1:/tmp/rep_b_out.json reports/qwen_repeat_b3.json

Ожидание при temperature=0.0 + seed=42: все 3 прогона побайтово совпадают.
"""

import asyncio
import json
import sys


async def one(adapter, text, call_id):
    from app.llm.port import AnalysisContext

    ctx = AnalysisContext(namespace="eval", call_id=call_id, speech_duration_sec=60.0)
    try:
        res = await adapter._analyze_via_llm(text, ctx)
        d = res.model_dump()
        return {"inquiry_type": d.get("inquiry_type"), "entities": d.get("entities", {})}
    except Exception as e:  # noqa: BLE001
        print(f"llm fallback on {call_id}: {type(e).__name__}", flush=True)
        return {"inquiry_type": "other", "entities": {}}


async def main(b_path: str, out_path: str) -> None:
    from app.llm.local_adapter import LocalLlamaCppAdapter

    set_b = json.load(open(b_path, encoding="utf-8"))
    adapter = LocalLlamaCppAdapter()
    passes = []
    for p in range(3):
        rows = []
        for it in set_b:
            r = await one(adapter, it["text"], f"{it['id']}#p{p + 1}")
            rows.append({"id": it["id"], **r})
            print(f"pass{p + 1} {it['id']}: {r['inquiry_type']}", flush=True)
        passes.append(rows)

    mismatches = []
    for i, it in enumerate(set_b):
        blobs = [json.dumps(passes[p][i], ensure_ascii=False, sort_keys=True) for p in range(3)]
        if not (blobs[0] == blobs[1] == blobs[2]):
            mismatches.append(it["id"])
    print(f"PASSSUMMARY total={len(set_b)} mismatched={len(mismatches)} {mismatches}")
    json.dump(
        {"passes": passes, "mismatched": mismatches},
        open(out_path, "w", encoding="utf-8"),
        ensure_ascii=False,
        indent=2,
    )


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
