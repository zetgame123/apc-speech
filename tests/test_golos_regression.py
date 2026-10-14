"""Регрессия чинок п.3 на открытом наборе Golos (только тексты эталонов, без ASR).

Проверяет, что нормалайзер и экстракторы количества/бюджета не деградируют
на 282 командных фразах: нет склеек «цифра+буква», нет ложных количеств,
бюджеты — только два заранее известных денежных упоминания
(golos_106 «пять тысяч рублей в долг», golos_130 «украсть миллион»).
Набор gitignored (data/eval_external/, ADR-017): без данных тест скипается.
"""

import json
import os
import re

import pytest

MANIFEST = os.path.join("data", "eval_external", "golos", "manifest.json")
KNOWN_BUDGET_IDS = {"golos_106", "golos_130"}

manifest = pytest.mark.skipif(
    not os.path.exists(MANIFEST), reason="нет данных Golos (data/eval_external/, ADR-017)"
)


def _load_refs():
    with open(MANIFEST, encoding="utf-8") as f:
        return json.load(f)


@manifest
def test_golos_no_digit_letter_glue():
    from app.asr.normalizer import RussianTextNormalizer

    norm = RussianTextNormalizer()
    bad = [
        (x["id"], norm.normalize(x["reference"]))
        for x in _load_refs()
        if re.search(r"\d[А-Яа-яA-Za-zЁё]", norm.normalize(x["reference"]))
    ]
    assert bad == [], f"склейки цифра+буква: {bad[:5]}"


@manifest
def test_golos_no_quantity_false_positives():
    from app.asr.normalizer import RussianTextNormalizer
    from app.llm.local_adapter import extract_quantity

    norm = RussianTextNormalizer()
    hits = [
        (x["id"], x["reference"])
        for x in _load_refs()
        if extract_quantity(norm.normalize(x["reference"]).lower()) is not None
    ]
    assert hits == [], f"ложные количества: {hits[:5]}"


@manifest
def test_golos_budget_only_known_money_mentions():
    from app.llm.local_adapter import extract_budget_rub

    hits = {x["id"] for x in _load_refs() if extract_budget_rub(x["reference"].lower()) is not None}
    assert hits == KNOWN_BUDGET_IDS, f"бюджет вне известных: {sorted(hits)}"
