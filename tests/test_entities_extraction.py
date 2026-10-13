"""Сущности period/budget/category rule-fallback: каждый формат из задачи."""

import pytest

from app.llm.local_adapter import extract_budget_rub, extract_category, extract_period


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("нужен щит на октябрь", "октябрь"),
        ("свободно в ноябре", "ноябрь"),
        ("размещение в декабре", "декабрь"),
        ("кампания на январь", "январь"),
        ("на первый квартал", "первый квартал"),
        ("аренда на полгода", "полгода"),
    ],
)
def test_extract_period_month(text, expected):
    assert extract_period(text.lower()) == expected


def test_extract_period_may_exact_forms():
    assert extract_period("кампания на май") == "май"
    assert extract_period("с мая по сентябрь") == "с мая по сентябрь"
    # стем «ма» не должен стрелять по «макет», «магазин»
    assert extract_period("какой макет нести") is None
    assert extract_period("мебельный магазин просит") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("с мая по сентябрь", "с мая по сентябрь"),
        ("кампания с июня по август", "с июня по август"),
    ],
)
def test_extract_period_date_range(text, expected):
    assert extract_period(text.lower()) == expected


def test_extract_period_ignores_bare_duration():
    assert extract_period("аренда на три месяца") is None
    assert extract_period("просто звонок") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("стоит 200 тысяч рублей", 200000),
        ("бюджет 200 000 руб.", 200000),
        ("до 80 тысяч рублей", 80000),
        ("договор на 1 000 000 руб.", 1000000),
    ],
)
def test_extract_budget_number_currency(text, expected):
    assert extract_budget_rub(text.lower()) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("бюджет полмиллиона рублей", 500000),
        ("полмиллиона за размещение", 500000),
        ("миллион рублей", 1000000),
        ("кампания на миллион", 1000000),
        ("на двести тысяч", 200000),
    ],
)
def test_extract_budget_word_amounts(text, expected):
    assert extract_budget_rub(text.lower()) == expected


def test_extract_budget_ignores_small_bare_numbers():
    assert extract_budget_rub("взять 2 поверхности") is None
    assert extract_budget_rub("просто звонок") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("счёт для автосалон", "автосалон"),
        ("мебельный магазин просит договор", "мебельный магазин"),
        ("открываем кафе", "кафе"),
        ("клиент фитнес-центр", "фитнес-центр"),
        ("застройщик из казани", "застройщик"),
    ],
)
def test_extract_category_from_dict(text, expected):
    assert extract_category(text.lower()) == expected


def test_extract_category_none():
    assert extract_category("просто звонок про щиты") is None


def test_rules_analysis_fills_period_budget_category():
    from app.llm.local_adapter import LocalLlamaCppAdapter
    from app.llm.port import AnalysisContext

    adapter = LocalLlamaCppAdapter()
    ctx = AnalysisContext(namespace="t", call_id="t", speech_duration_sec=60.0)
    res = adapter._analyze_via_rules(
        "Клиент: сколько стоит щит в Балашихе на октябрь\n"
        "Менеджер: Добрый день, щит стоит двести тысяч рублей, договор для автосалон вышлю",
        ctx,
    ).model_dump()
    ent = res["entities"]
    assert ent["target_period"] == "октябрь"
    assert ent["budget_rub"] == 200000
    assert ent["product_category"] == "автосалон"
