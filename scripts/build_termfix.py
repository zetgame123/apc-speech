#!/usr/bin/env python3
"""П.4: таблица путаниц из наблюдаемых ошибок + проверка split-половинами.

Метод: TTS-20 делится на A (шаблоны 0-4: term_00-04 + term_10-14) и B
(шаблоны 5-9: term_05-09 + term_15-19). Половины не пересекаются по фразам
(внутри каждой — два одинаковых цикла шаблонов, это зафиксировано, а не
скрыто). Пары путаниц извлекаются ТОЛЬКО из ошибок на A; B — held-out.
Ложные замены меряются на кэше гипотез Golos-100 (podlodka, оба условия):
слово, верное до поправки и испорченное после, — ложное срабатывание.

Выход: reports/termfix.json. Сверка с app/asr/term_fixer.py:CONFUSION_PAIRS.
"""

import argparse
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.asr.engine import AsrEngine
from app.asr.term_fixer import CONFUSION_PAIRS, apply_term_fixes
from evaluation.wer_cer import align_words, normalize_eval_text


def _load_terms() -> list[str]:
    import yaml

    with open(os.path.join("config", "vocabulary_hints.yaml"), encoding="utf-8") as f:
        hints = yaml.safe_load(f)
    return [str(t) for v in hints.values() if isinstance(v, list) for t in v]


def _transcribe_base(eng: AsrEngine, wav_path: str) -> str:
    with open(wav_path, "rb") as f:
        segs, _ = eng.transcribe(
            io.BytesIO(f.read()),
            language="ru",
            beam_size=2,
            temperature=0.0,
            vad_filter=True,
            word_timestamps=False,
        )
    return " ".join(s["text"] for s in segs)


def _term_share(items: list[dict], hyps: dict[str, str], terms: list[str]) -> str:
    hit = tot = 0
    for item in items:
        ref_n = normalize_eval_text(item["reference"])
        hyp_n = normalize_eval_text(hyps[item["id"]])
        expected = [t for t in terms if normalize_eval_text(t) and normalize_eval_text(t) in ref_n]
        hit += sum(1 for t in expected if normalize_eval_text(t) in hyp_n)
        tot += len(expected)
    return f"{hit}/{tot}={round(hit / tot, 3) if tot else 0}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="reports/termfix.json")
    parser.add_argument("--golos-hyps", default="reports/wer_hyps_golos100.json")
    parser.add_argument("--golos-manifest", default="data/eval_external/golos/manifest.json")
    parser.add_argument("--golos-n", type=int, default=100)
    args = parser.parse_args()

    terms = _load_terms()
    term_singles = {
        normalize_eval_text(t)
        for t in terms
        if normalize_eval_text(t) and " " not in normalize_eval_text(t)
    }
    with open(os.path.join("tmp", "eval_terms", "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    by_id = {row["id"]: row["reference"] for row in manifest}
    hyps: dict[str, str] = {}

    eng = AsrEngine(model_name="./models/whisper-podlodka-turbo-ct2")
    for row in manifest:
        wav = os.path.join("tmp", "eval_terms", row["id"] + ".wav")
        hyps[row["id"]] = _transcribe_base(eng, wav)
        print(f"{row['id']}: {hyps[row['id']][:70]}", flush=True)

    idx_a = [f"term_{i:02d}" for i in list(range(0, 5)) + list(range(10, 15))]
    idx_b = [f"term_{i:02d}" for i in list(range(5, 10)) + list(range(15, 20))]
    assert set(idx_a) & set(idx_b) == set(), "половины пересекаются"
    assert {r["id"] for r in manifest} == set(idx_a) | set(idx_b), "потеряны фразы"

    derived: dict[str, str] = {}
    for tid in idx_a:
        ref_n = normalize_eval_text(by_id[tid])
        hyp_n = normalize_eval_text(hyps[tid])
        for op, ref_w, hyp_w in align_words(ref_n.split(), hyp_n.split()):
            if op == "sub" and ref_w in term_singles and hyp_w not in term_singles:
                surface = next(
                    (t for t in terms if normalize_eval_text(t) == ref_w),
                    ref_w,
                )
                derived.setdefault(hyp_w, surface)
    print("derived pairs (A):", derived, flush=True)
    frozen_ok = derived == CONFUSION_PAIRS
    print(f"frozen table match: {frozen_ok}", flush=True)

    items_a = [{"id": t, "reference": by_id[t]} for t in idx_a]
    items_b = [{"id": t, "reference": by_id[t]} for t in idx_b]
    items_all = items_a + items_b
    fixed = {tid: apply_term_fixes(h) for tid, h in hyps.items()}
    share = {
        "A_before": _term_share(items_a, hyps, terms),
        "A_after": _term_share(items_a, fixed, terms),
        "B_before": _term_share(items_b, hyps, terms),
        "B_after": _term_share(items_b, fixed, terms),
        "all_before": _term_share(items_all, hyps, terms),
        "all_after": _term_share(items_all, fixed, terms),
    }
    print("shares:", share, flush=True)

    with open(args.golos_hyps, encoding="utf-8") as f:
        golos_hyps = json.load(f)["hyps"]
    with open(args.golos_manifest, encoding="utf-8") as f:
        golos_items = json.load(f)[: args.golos_n]
    false = ok_total = changed_total = 0
    for item in golos_items:
        ref_n = normalize_eval_text(item["reference"])
        for cond in ("clean", "telephony"):
            hyp = golos_hyps[f"podlodka/{item['id']}/{cond}"]
            hyp_n = normalize_eval_text(hyp)
            fixed_n = normalize_eval_text(apply_term_fixes(hyp))
            if fixed_n != hyp_n:
                changed_total += 1
            ok_before = {w for op, w, _ in align_words(ref_n.split(), hyp_n.split()) if op == "ok"}
            ok_after = {w for op, w, _ in align_words(ref_n.split(), fixed_n.split()) if op == "ok"}
            # Грубая, но честная метрика: верных слов стало меньше — есть ложные.
            false += max(0, len(ok_before) - len(ok_after))
            ok_total += len(ok_before)
    fp_rate = round(false / ok_total, 4) if ok_total else 0.0
    print(
        f"golos false replacements: {false}/{ok_total}={fp_rate} (touched files: {changed_total})",
        flush=True,
    )

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(
            {
                "split": {"A": idx_a, "B": idx_b},
                "derived_pairs_A": derived,
                "frozen_match": frozen_ok,
                "shares": share,
                "golos_fp": {
                    "false": false,
                    "ok_total": ok_total,
                    "rate": fp_rate,
                    "touched": changed_total,
                },
            },
            f,
            ensure_ascii=False,
            indent=2,
        )


if __name__ == "__main__":
    main()
