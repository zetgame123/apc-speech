#!/usr/bin/env python3
"""Аудит WER: варианты нормализации + топ ошибок (замены/вставки/пропуски).

Вход: манифест + кэш гипотез (reports/wer_hyps_*.json от scripts/eval_wer.py
с --save-hyps). Никакой транскрипции — только пересчёт, дёшево и детерминировано.
Выход: reports/wer_variants.json, reports/wer_errors.json. Печать таблиц в stdout.
"""

import argparse
import json
import os
from collections import Counter

from evaluation.wer_cer import (
    NORMALIZATIONS,
    align_words,
    levenshtein_distance,
)


def corpus_wer(items: list[dict], hyps: dict, eng: str, cond: str, norm_fn) -> tuple[float, float]:
    err_w = ref_w = err_c = ref_c = 0
    for item in items:
        hyp = hyps[f"{eng}/{item['id']}/{cond}"]
        rn, hn = norm_fn(item["reference"]), norm_fn(hyp)
        rw, hw = rn.split(), hn.split()
        err_w += levenshtein_distance(rw, hw)
        ref_w += len(rw)
        err_c += levenshtein_distance(list(rn), list(hn))
        ref_c += len(rn)
    return (round(err_w / ref_w, 4) if ref_w else 0.0, round(err_c / ref_c, 4) if ref_c else 0.0)


def error_tops(items: list[dict], hyps: dict, eng: str, cond: str, norm_fn, top: int = 50) -> dict:
    subs: Counter = Counter()
    ins: Counter = Counter()
    dels: Counter = Counter()
    n_sub = n_ins = n_del = n_ok = 0
    for item in items:
        hyp = hyps[f"{eng}/{item['id']}/{cond}"]
        rw = norm_fn(item["reference"]).split()
        hw = norm_fn(hyp).split()
        for op, r, h in align_words(rw, hw):
            if op == "ok":
                n_ok += 1
            elif op == "sub":
                n_sub += 1
                subs[f"{r} -> {h}"] += 1
            elif op == "ins":
                n_ins += 1
                ins[f"+ {h}"] += 1
            else:
                n_del += 1
                dels[f"{r} -> ∅"] += 1
    return {
        "counts": {"ok": n_ok, "sub": n_sub, "ins": n_ins, "del": n_del},
        "top_sub": subs.most_common(top),
        "top_ins": ins.most_common(top),
        "top_del": dels.most_common(top),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="data/eval_external/golos/manifest.json")
    parser.add_argument("--n", type=int, default=100)
    parser.add_argument("--hyps", default="reports/wer_hyps_golos100.json")
    parser.add_argument("--engines", default="podlodka,whisper-small,t-one")
    parser.add_argument("--top", type=int, default=50)
    parser.add_argument("--out-variants", default="reports/wer_variants.json")
    parser.add_argument("--out-errors", default="reports/wer_errors.json")
    args = parser.parse_args()

    with open(args.manifest, encoding="utf-8") as f:
        items = json.load(f)[: args.n]
    with open(args.hyps, encoding="utf-8") as f:
        hyps = json.load(f)["hyps"]

    variants: dict = {}
    for eng in args.engines.split(","):
        eng = eng.strip()
        for cond in ("clean", "telephony"):
            variants.setdefault(eng, {})[cond] = {}
            for vname, fn in NORMALIZATIONS.items():
                wer, cer = corpus_wer(items, hyps, eng, cond, fn)
                variants[eng][cond][vname] = {"wer": wer, "cer": cer}
            row = variants[eng][cond]
            print(
                f"{eng}/{cond}: " + " ".join(f"{k}={v['wer']}" for k, v in row.items()),
                flush=True,
            )
    os.makedirs(os.path.dirname(args.out_variants) or ".", exist_ok=True)
    with open(args.out_variants, "w", encoding="utf-8") as f:
        json.dump(
            {"n": len(items), "hyps": args.hyps, "variants": variants},
            f,
            ensure_ascii=False,
            indent=2,
        )

    errors: dict = {}
    for eng in args.engines.split(","):
        eng = eng.strip()
        for cond in ("clean", "telephony"):
            errors.setdefault(eng, {})[cond] = error_tops(
                items, hyps, eng, cond, NORMALIZATIONS["default"], top=args.top
            )
            c = errors[eng][cond]["counts"]
            print(f"{eng}/{cond}: ok={c['ok']} sub={c['sub']} ins={c['ins']} del={c['del']}")
    with open(args.out_errors, "w", encoding="utf-8") as f:
        json.dump({"n": len(items), "errors": errors}, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
