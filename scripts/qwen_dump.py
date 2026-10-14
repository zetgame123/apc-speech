#!/usr/bin/env python3
"""Дамп сырых разборов Qwen (Asub-30 + B-25) внутри api-контейнера.

Использование:
  docker cp tmp/type_suite/set_a.json apc_small-api-1:/tmp/dump_a.json
  docker cp tmp/type_suite/set_b.json apc_small-api-1:/tmp/dump_b.json
  docker cp scripts/qwen_dump.py apc_small-api-1:/tmp/qwen_dump.py
  docker exec -e APP_ENV=development -e DEV_TOKEN=dev-token apc_small-api-1 \\
    python /tmp/qwen_dump.py /tmp/dump_a.json /tmp/dump_b.json /tmp/qwen_dump_out.json
  docker cp apc_small-api-1:/tmp/qwen_dump_out.json reports/qwen_dump_v14.json
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


async def main(a_path: str, b_path: str, out_path: str) -> None:
    from app.llm.local_adapter import LocalLlamaCppAdapter

    set_a = json.load(open(a_path, encoding="utf-8"))
    set_b = json.load(open(b_path, encoding="utf-8"))
    sub_a = set_a[::4][:30]
    adapter = LocalLlamaCppAdapter()
    out = {}
    for name, items in (("qwen_Asub", sub_a), ("qwen_B", set_b)):
        rows = []
        for it in items:
            r = await one(adapter, it["text"], it["id"])
            rows.append(
                {
                    "id": it["id"],
                    "expected_type": it["type"],
                    "pred_type": r["inquiry_type"],
                    "expected_entities": it["entities"],
                    "pred_entities": r["entities"],
                }
            )
            print(f"{name} {it['id']}: {it['type']}->{r['inquiry_type']}", flush=True)
        out[name] = rows
    json.dump(out, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3]))
