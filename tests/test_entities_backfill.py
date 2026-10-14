"""П.4: добивка сущностей Qwen правилами на уровне полей + закрытый список категорий."""

import yaml

from app.llm.local_adapter import (
    PRODUCT_CATEGORY_DICT,
    LocalLlamaCppAdapter,
    backfill_entities_from_rules,
    canonical_category,
)


def test_backfill_fills_empty_fields_from_text():
    entities = {
        "cities": [],
        "formats": [],
        "quantity": None,
        "target_period": None,
        "budget_rub": None,
        "product_category": None,
    }
    text = "Клиент: нужен щит в Химках на октябрь\nМенеджер: выйдет двести тысяч рублей"
    out = backfill_entities_from_rules(dict(entities), text)
    assert out["cities"] == ["Химки"]
    assert "щит" in out["formats"]
    assert out["target_period"] == "октябрь"
    assert out["budget_rub"] == 200000
    # Категории в тексте нет — остаётся null, а не галлюцинация.
    assert out["product_category"] is None


def test_backfill_does_not_overwrite_model_values():
    entities = {
        "cities": ["Москва"],
        "formats": ["экран"],
        "quantity": 5,
        "target_period": "ноябрь",
        "budget_rub": 1000000,
        "product_category": "кафе",
    }
    text = "Клиент: нужен щит в Химках на октябрь за двести тысяч, автосалон"
    out = backfill_entities_from_rules(dict(entities), text)
    assert out["cities"] == ["Москва"]
    assert out["formats"] == ["экран"]
    assert out["quantity"] == 5
    assert out["target_period"] == "ноябрь"
    assert out["budget_rub"] == 1000000
    assert out["product_category"] == "кафе"


def test_invalid_category_nulled_and_backfilled():
    # Галлюцинация модели («суперсайт») вне закрытого списка — обнуляется,
    # затем добивается правилами из текста («автосалон»).
    entities = {"cities": [], "formats": [], "product_category": "суперсайт"}
    out = backfill_entities_from_rules(dict(entities), "Клиент: наш автосалон в Казани")
    assert out["product_category"] == "автосалон"


def test_invalid_category_without_text_evidence_stays_null():
    entities = {"cities": [], "formats": [], "product_category": "formats"}
    out = backfill_entities_from_rules(dict(entities), "Клиент: просто звонок")
    assert out["product_category"] is None


def test_canonical_category_accepts_alias_and_rejects_substring():
    assert canonical_category("мебельный") == "мебельный магазин"
    assert canonical_category("  Застройщик ") == "застройщик"
    assert canonical_category("суперсайт") is None
    assert canonical_category("next_month") is None
    assert canonical_category("") is None
    assert canonical_category(None) is None
    assert canonical_category(42) is None


def test_category_closed_list_comes_from_config():
    with open("config/product_categories.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    # Конфиг — источник правды: встроенный дефолт обязан совпадать.
    assert cfg == PRODUCT_CATEGORY_DICT
    adapter = LocalLlamaCppAdapter()
    assert adapter.categories == PRODUCT_CATEGORY_DICT
    # Валидация идёт именно по конфигу, а не по коду.
    assert canonical_category("аптека", adapter.categories) == "аптека"
    assert canonical_category("щит", adapter.categories) is None
