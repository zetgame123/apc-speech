#!/usr/bin/env python3
"""П.3: 5 фиксированных разборов через llm-runtime, замер wall-clock.

Запуск внутри api-контейнера (видит llm-runtime по имени):
  docker cp scripts/bench_llm_5.py apc_small-api-1:/tmp/bench_llm_5.py
  docker exec -e APP_ENV=development -e DEV_TOKEN=dev-token -e PYTHONPATH=/tmp/ov \\
    apc_small-api-1 python /tmp/bench_llm_5.py /tmp/dump_b.json 5
Образец: первые 5 диалогов набора B (короткие, фиксированные).
Переключение -t — через пересоздание llm-runtime (см. docs/eval-report.md п.3).
"""

import asyncio
import json
import sys
import time


async def main(b_path: str, n: int) -> None:
    from app.llm.local_adapter import LocalLlamaCppAdapter
    from app.llm.port import AnalysisContext

    set_b = json.load(open(b_path, encoding="utf-8"))[:n]
    adapter = LocalLlamaCppAdapter()
    times = []
    for it in set_b:
        ctx = AnalysisContext(
            namespace="bench", call_id=f"bench-{it['id']}", speech_duration_sec=60.0
        )
        t0 = time.monotonic()
        try:
            await adapter._analyze_via_llm(it["text"], ctx)
            ok = True
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"{it['id']}: ERROR {type(e).__name__}", flush=True)
        dt = time.monotonic() - t0
        if ok:
            times.append(dt)
            print(f"{it['id']}: {dt:.1f} c", flush=True)
    times.sort()
    med = times[len(times) // 2] if times else float("nan")
    print(
        json.dumps(
            {
                "n": len(times),
                "times_sec": [round(t, 1) for t in times],
                "median_sec": round(med, 1),
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 5))
