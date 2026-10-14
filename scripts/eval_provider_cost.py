#!/usr/bin/env python3
"""C10-7: расчётная стоимость внешнего провайдера аналитики.

Реальных вызовов НЕ делает (адаптер выключен, ключа нет). Считает по
замеренным токенам локального разбора (представительный вызов v1.3b:
prompt ~802, gen ~235) и ценам, которые задаёт пользователь
(аргументы/env PROVIDER_PRICE_*_USD_PER_1M). Фактическая стоимость
провайдера: не измерено (нет ключа, адаптер выключен по умолчанию).
"""

import argparse
import json
import os

MEASURED_PROMPT_TOKENS = 802
MEASURED_GEN_TOKENS = 235
MEASURE_NOTE = (
    "один представительный разбор v1.3b (set_a[0], локальный Qwen2.5-7B, "
    "server-side prompt eval; часовые транскрипты урезаются адаптером до ~4000 символов)"
)


def provider_cost(
    prompt_tokens: int,
    gen_tokens: int,
    price_in_per_1m: float,
    price_out_per_1m: float,
    calls: int,
) -> dict:
    per_call = prompt_tokens / 1e6 * price_in_per_1m + gen_tokens / 1e6 * price_out_per_1m
    return {
        "per_call_usd": round(per_call, 6),
        "per_1000_calls_usd": round(per_call * 1000, 4),
        "calls": calls,
        "total_usd": round(per_call * calls, 4),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt-tokens", type=int, default=MEASURED_PROMPT_TOKENS)
    ap.add_argument("--gen-tokens", type=int, default=MEASURED_GEN_TOKENS)
    ap.add_argument(
        "--price-in",
        type=float,
        default=float(os.environ.get("PROVIDER_PRICE_IN_USD_PER_1M", "0") or 0),
    )
    ap.add_argument(
        "--price-out",
        type=float,
        default=float(os.environ.get("PROVIDER_PRICE_OUT_USD_PER_1M", "0") or 0),
    )
    ap.add_argument("--calls-per-month", type=int, default=1000)
    ap.add_argument("--out", default="reports/provider_cost.json")
    args = ap.parse_args()

    calc = provider_cost(
        args.prompt_tokens,
        args.gen_tokens,
        args.price_in,
        args.price_out,
        args.calls_per_month,
    )
    rep = {
        "measured_tokens": {
            "prompt": args.prompt_tokens,
            "gen": args.gen_tokens,
            "note": MEASURE_NOTE,
        },
        "prices_usd_per_1m": {"input": args.price_in, "output": args.price_out},
        "calculation": calc,
        "actual_provider_cost": "не измерено, причина: нет ключа, адаптер выключен",
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)
    print(json.dumps(calc, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
