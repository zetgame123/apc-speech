#!/usr/bin/env python3
"""Зонд Qwen: 10 диалогов -> сырые entities (для таблицы эталон/получено).

Запуск внутри api-контейнера (доступ к llm-runtime по имени):
  docker cp tmp/type_suite/set_a.json apc_small-api-1:/tmp/probe_a.json
  docker cp scripts/probe_qwen10.py apc_small-api-1:/tmp/probe_qwen10.py
  docker exec -e APP_ENV=development -e DEV_TOKEN=dev-token apc_small-api-1 \
    python /tmp/probe_qwen10.py /tmp/probe_a.json /tmp/probe_qwen10_out.json
  docker cp apc_small-api-1:/tmp/probe_qwen10_out.json reports/probe_qwen10_out.json
"""

import asyncio
import json
import sys


async def main(ids_path: str, out_path: str, limit: int = 10) -> None:
    from app.llm.local_adapter import LocalLlamaCppAdapter
    from app.llm.port import AnalysisContext

    items = json.load(open(ids_path, encoding="utf-8"))[:limit]
    adapter = LocalLlamaCppAdapter()
    print(f"probe: endpoint={adapter.endpoint_url} model={adapter.model_name}", flush=True)
    out = []
    for it in items:
        ctx = AnalysisContext(
            namespace="eval", call_id=f"probe-{it['id']}", speech_duration_sec=60.0
        )
        try:
            res = await adapter._analyze_via_llm(it["text"], ctx)
            ent = res.model_dump().get("entities", {})
        except Exception as e:  # noqa: BLE001
            ent = {"_error": f"{type(e).__name__}: {e}"}
        row = {
            "id": it["id"],
            "type": it["type"],
            "expected_entities": it["entities"],
            "qwen_entities": ent,
        }
        out.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    json.dump(out, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
