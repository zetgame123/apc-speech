"""Сущности period/budget/category rule-fallback: каждый формат из задачи."""

import pytest

from app.llm.local_adapter import (
    canonical_period,
    extract_budget_rub,
    extract_category,
    extract_period,
)


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


def test_extract_period_relative_season_duration():
    # Относительные (ADR-021/022): эталоны b000/b015.
    assert extract_period("подскажите по занятости на следующий месяц") == "следующий месяц"
    assert extract_period("в следующем месяце") == "следующий месяц"
    assert extract_period("в следующем квартале") == "следующий квартал"
    # Сезоны: эталон b013.
    assert extract_period("На весну, призматрон бы".lower()) == "весна"
    assert extract_period("кампания на лето") == "лето"
    assert extract_period("размещение осенью") == "осень"
    assert extract_period("аренда на зиму") == "зима"
    # Длительности — тоже период (старое «голые не считаются» отменено):
    # эталоны b001/b016; вход может быть сырым или после нормалайзера.
    assert extract_period("В Мытищах, месяца на три".lower()) == "3 месяца"
    assert extract_period("аренда на три месяца") == "3 месяца"
    assert extract_period("аренда на 3 мес.") == "3 месяца"
    assert extract_period("размещение на два месяца") == "2 месяца"
    assert extract_period("договор на пять месяцев") == "5 месяцев"
    assert extract_period("контракт на один месяц") == "1 месяц"
    # «лето» не стреляет по «билет», «май» — по «макет».
    assert extract_period("купи билет") is None
    assert extract_period("какой макет нести") is None


def test_canonical_period_unifies_forms():
    assert canonical_period("три месяца") == "3 месяца"
    assert canonical_period("месяца на три") == "3 месяца"
    assert canonical_period("на 3 мес.") == "3 месяца"
    assert canonical_period("на следующий месяц") == "следующий месяц"
    assert canonical_period("следующий месяц") == "следующий месяц"
    assert canonical_period("На весну") == "весна"
    assert canonical_period("весна") == "весна"
    assert canonical_period("октябрь") == "октябрь"
    assert canonical_period("просто звонок") is None


def test_extract_period_ignores_non_period():
    assert extract_period("просто звонок") is None
    assert extract_period("аренда на месяцы") is None


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


def test_extract_budget_year_is_not_budget():
    # «две тысячи четвертый год» — год (Golos-294), а не деньги,
    # даже после чинки склеек нормалайзера («2 000 четвертый год»).
    assert extract_budget_rub("джой две тысячи четвертый год это часть чего") is None
    # А «на двести тысяч» без валюты — бюджет (эталон b014, ADR-021).
    assert (
        extract_budget_rub("Клиент: В Подольске, на двести тысяч\nМенеджер: Отменяю".lower())
        == 200000
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("взять 2 поверхности", 2),
        ("Это экраны, два штук", 2),
        ("нужны две поверхности", 2),
        ("сторона Б сойдёт", None),  # сторона ≠ количество (ADR-021)
        ("Есть один, бронирую за вами", None),  # наличие менеджера (ADR-021)
        ("Один свободен", None),  # наличие менеджера (ADR-021)
        ("Есть пара вариантов", None),  # vague (ADR-021)
        ("просто звонок", None),
    ],
)
def test_extract_quantity_words_and_no_manager_counts(text, expected):
    from app.asr.normalizer import RussianTextNormalizer
    from app.llm.local_adapter import extract_quantity

    normed = RussianTextNormalizer().normalize(text).lower()
    assert extract_quantity(normed) == expected


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
