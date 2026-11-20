import re
from typing import Any

from app.asr.normalizer import NUMBER_WORDS, get_normalizer


def coerce_quantity(value: Any) -> int | None:
    """Мягкое приведение entities.quantity к integer|null (только однозначное).

    Цифры строкой ("6", "1 000") и числительные через нормалайзер проекта
    ("двести тысяч" -> "200 000") и NUMBER_WORDS ("три" -> 3). Остальное
    (слова без чисел, bool, дроби, мусор) -> None, а не падение схемы.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        s = value.strip().lower().replace("ё", "е").replace(" ", " ")
        if not s:
            return None
        nospace = re.sub(r"\s+", "", s)
        try:
            return int(nospace)
        except ValueError:
            pass
        try:
            as_float = float(nospace.replace(",", "."))
            return int(as_float) if as_float.is_integer() else None
        except ValueError:
            pass
        normed = get_normalizer().normalize(s)
        digit_run = re.search(r"\d[\d\s]*", normed)
        if digit_run:
            try:
                return int(re.sub(r"\s+", "", digit_run.group(0)))
            except ValueError:
                pass
        if s in NUMBER_WORDS:
            return NUMBER_WORDS[s]
        return None
    return None


def coerce_budget(value: Any) -> float | int | None:
    """Мягкое приведение entities.budget_rub к number|null (аналог quantity).

    Понимает текстовые множители: «тысяч» (×1000), «миллион» (×1e6),
    «миллиард» (×1e9), «пол-» (×0.5: «полмиллиона» → 500000).
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        s = value.strip().lower().replace("ё", "е").replace(" ", " ")
        if not s:
            return None
        nospace = re.sub(r"\s+", "", s)
        try:
            return int(nospace)
        except ValueError:
            pass
        try:
            return float(nospace.replace(",", "."))
        except ValueError:
            pass
        scaled = _parse_scaled_amount(s)
        if scaled is not None:
            return scaled
        normed = get_normalizer().normalize(s)
        digit_run = re.search(r"\d[\d\s]*[.,]?\d*", normed)
        if digit_run:
            try:
                num = float(digit_run.group(0).replace(" ", "").replace(",", "."))
                return int(num) if num.is_integer() else num
            except ValueError:
                pass
        if s in NUMBER_WORDS:
            return NUMBER_WORDS[s]
        return None
    return None


_AMOUNT_MULTIPLIERS = (
    (re.compile(r"миллиард[а-я]*"), 1_000_000_000),
    (re.compile(r"миллион[а-я]*"), 1_000_000),
    (re.compile(r"млрд\.?"), 1_000_000_000),
    (re.compile(r"млн\.?"), 1_000_000),
    (re.compile(r"тысяч[а-я]*|тыс\.?"), 1_000),
)


def _parse_scaled_amount(s: str) -> float | int | None:
    """Число с русским множителем: «двести тысяч» → 200000, «полмиллиона» → 500000.

    Возвращает None, если множителя нет или числовая часть не распознана.
    """
    mult = 1
    rest = s
    for pat, factor in _AMOUNT_MULTIPLIERS:
        m = pat.search(s)
        if m:
            mult = factor
            rest = (s[: m.start()] + " " + s[m.end() :]).strip()
            break
    else:
        return None
    if re.search(r"\bполутора\b|\bполторы\b|\bполтора\b", rest):
        base = 1.5
        rest = re.sub(r"\bполутора\b|\bполторы\b|\bполтора\b", " ", rest)
        rest = re.sub(r"\bрубл[а-яё]*\b", " ", rest)
        rest = re.sub(r"[^0-9а-яё\s.,]", " ", rest)
        rest = re.sub(r"\s+", " ", rest).strip()
        if rest:
            return None
        result = base * mult
        return int(result) if float(result).is_integer() else result
    half = bool(re.search(r"\bпол[уы]?\b", rest))
    rest = re.sub(r"\bпол[уы]?\b", " ", rest)
    rest = re.sub(r"\bрубл[а-яё]*\b", " ", rest)
    rest = re.sub(r"[^0-9а-яё\s.,]", " ", rest)
    rest = re.sub(r"\s+", " ", rest).strip()
    if not rest:
        base: float | None = 0.5 if half else 1.0
    else:
        digits = re.sub(r"\s+", "", rest)
        try:
            base = int(digits)
        except ValueError:
            try:
                base = float(digits.replace(",", "."))
            except ValueError:
                base = _sum_number_words(rest)
                if base is None:
                    return None
                if half:
                    base += 0.5
                elif base == 0:
                    base = 1.0
    result = base * mult
    if half and not rest:
        result = 0.5 * mult
    return int(result) if float(result).is_integer() else result


def _sum_number_words(rest: str) -> float | None:
    total = 0
    found = False
    for w in rest.split():
        if w in NUMBER_WORDS:
            total += NUMBER_WORDS[w]
            found = True
        elif w.isdigit():
            total += int(w)
            found = True
    return total if found else None


# Словарь категорий бизнеса клиента: каноническая форма -> алиасы в нижнем регистре.
# Покрывает эталоны оценочного набора (автосалон, мебельный магазин, кафе,
# фитнес-центр, застройщик) + базовые категории реальных звонков.
PRODUCT_CATEGORY_DICT: dict[str, list[str]] = {
    "автосалон": ["автосалон"],
    "мебельный магазин": ["мебельный магазин", "мебельный"],
    "кафе": ["кафе"],
    "фитнес-центр": ["фитнес-центр", "фитнес центр", "фитнес"],
    "застройщик": ["застройщик"],
    "ресторан": ["ресторан"],
    "гостиница": ["гостиница", "отель"],
    "автосервис": ["автосервис"],
    "салон красоты": ["салон красоты"],
    "аптека": ["аптека"],
}

# Канонические месяцы для нормализации периода (именительный падеж).
RU_MONTHS_CANON = [
    "январь",
    "февраль",
    "март",
    "апрель",
    "май",
    "июнь",
    "июль",
    "август",
    "сентябрь",
    "октябрь",
    "ноябрь",
    "декабрь",
]

_MONTH_STEM_TO_CANON = {
    "январ": "январь",
    "феврал": "февраль",
    "март": "март",
    "апрел": "апрель",
    "июн": "июнь",
    "июл": "июль",
    "август": "август",
    "сентябр": "сентябрь",
    "октябр": "октябрь",
    "ноябр": "ноябрь",
    "декабр": "декабрь",
}

# «май» — только точные формы: стем «ма» давал бы ложные «макет», «магазин».
_MAY_FORMS = re.compile(r"\bмай\b|\bмая\b|\bмае\b|\bмаю\b|\bмаем\b", re.IGNORECASE)

# Сезоны: флексии → каноника (именительный падеж). \b-границы исключают
# ложные («лето» не стреляет по «билет», «зима» — по «магазин»).
_SEASON_TO_CANON = (
    (re.compile(r"\bвесну\b|\bвесн[аыойеу]\b|\bвесной\b"), "весна"),
    (re.compile(r"\bлето\b|\bлетом\b"), "лето"),
    (re.compile(r"\bосень\b|\bосенью\b"), "осень"),
    (re.compile(r"\bзиму\b|\bзимой\b|\bзима\b|\bзиме\b"), "зима"),
)

# Длительности в месяцах: число (цифры или слово) + единица в любом порядке.
# Покрывает «на три месяца», «на 3 мес.» (выход нормалайзера) и инверсию
# «месяца на три». Возвращаем канонику «N <plural>» (3 месяца, 1 месяц,
# 5 месяцев), чтобы «три месяца» == «на 3 мес.» == «месяца на три».
_DURATION_WORD_TO_NUM = {
    "один": 1,
    "одна": 1,
    "одно": 1,
    "два": 2,
    "две": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
    "десять": 10,
    "одиннадцать": 11,
    "двенадцать": 12,
}
_DURATION_UNIT_PAT = r"(?:месяц[а-я]*|мес\.?)"
_DURATION_NUM_PAT = r"(?:\d+|один|одна|одно|два|две|три|четыре|пять|шесть|семь|восемь|девять|десять|одиннадцать|двенадцать)"
_DURATION_DIRECT = re.compile(
    r"\b(" + _DURATION_NUM_PAT + r")\s*" + _DURATION_UNIT_PAT + r"\b",
    re.IGNORECASE,
)
_DURATION_INVERTED = re.compile(
    _DURATION_UNIT_PAT + r"\s+на\s+(" + _DURATION_NUM_PAT + r")\b",
    re.IGNORECASE,
)


def _plural_month(n: int) -> str:
    """Русская плюрализация месяца: 1 месяц, 3 месяца, 5 месяцев."""
    if n % 10 == 1 and n % 100 != 11:
        return "месяц"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "месяца"
    return "месяцев"


def _duration_num_to_int(raw: str) -> int | None:
    raw = raw.strip().lower()
    if raw.isdigit():
        return int(raw)
    return _DURATION_WORD_TO_NUM.get(raw)


def _extract_duration_months(text_lower: str) -> str | None:
    """Длительность «N месяцев» каноникой «N <plural>» или None."""
    m = _DURATION_DIRECT.search(text_lower)
    if m:
        n = _duration_num_to_int(m.group(1))
        if n is not None and 1 <= n <= 120:
            return f"{n} {_plural_month(n)}"
    m = _DURATION_INVERTED.search(text_lower)
    if m:
        n = _duration_num_to_int(m.group(1))
        if n is not None and 1 <= n <= 120:
            return f"{n} {_plural_month(n)}"
    return None


def canonical_period(value: object) -> str | None:
    """Каноническая форма периода для сравнения эталона и предсказания.

    «на следующий месяц»/«в следующем месяце» → «следующий месяц»;
    «в следующем квартале» → «следующий квартал»; сезоны → именительный
    («На весну» → «весна»); длительности → «N <plural>» («три месяца»,
    «месяца на три», «на 3 мес.» → «3 месяца»); месяцы/кварталы/диапазоны —
    как в экстракторе. Не период → None.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return None
    s = str(value).strip().lower().replace("ё", "е")
    if not s:
        return None
    s = re.sub(r"\s+", " ", s)
    if re.search(r"следующ[а-я]*\s+месяц[а-я]*", s):
        return "следующий месяц"
    if re.search(r"следующ[а-я]*\s+квартал[а-я]*", s):
        return "следующий квартал"
    for pat, canon in _SEASON_TO_CANON:
        if pat.search(s):
            return canon
    dur = _extract_duration_months(s)
    if dur is not None:
        return dur
    if re.search(r"\bполгода\b|\bпол\s+года\b", s):
        return "полгода"
    if re.search(r"\bна\s+год\b", s):
        return "год"
    m = re.search(
        r"(первый|второй|третий|четвертый|1-й|2-й|3-й|4-й)\s+квартал[а-я]*",
        s,
    )
    if m:
        return m.group(0)
    m = re.search(r"с\s+([а-я]+)\s+по\s+([а-я]+)", s)
    if m:
        return m.group(0)
    if _MAY_FORMS.search(s):
        return "май"
    for stem, canon in _MONTH_STEM_TO_CANON.items():
        if re.search(r"\b" + stem + r"[а-я]*\b", s):
            return canon
    return None


def extract_period(text_lower: str) -> str | None:
    """Период размещения каноникой: месяц, квартал, полгода, диапазон,
    относительный период, сезон, длительность.

    Примеры: «на октябрь» → «октябрь»; «первый квартал» →
    «первый квартал»; «на полгода» → «полгода»;
    «с мая по сентябрь» → «с мая по сентябрь»;
    «на следующий месяц» → «следующий месяц»;
    «в следующем квартале» → «следующий квартал»;
    «На весну» → «весна»; «месяца на три» → «3 месяца».
    По ADR-021/022 длительности — тоже период (старое правило
    «голые длительности не считаются» отменено).
    """
    if not isinstance(text_lower, str):
        return None
    s = text_lower.lower()
    m = re.search(r"с\s+([а-яё]+)\s+по\s+([а-яё]+)", s)
    if m:
        return m.group(0)
    if re.search(r"следующ[а-яё]*\s+квартал[а-яё]*", s):
        return "следующий квартал"
    m = re.search(
        r"(первый|второй|третий|четвертый|четвёртый|1-й|2-й|3-й|4-й)\s+квартал",
        s,
    )
    if m:
        return m.group(0).replace("ё", "е")
    if re.search(r"следующ[а-яё]*\s+месяц[а-яё]*", s):
        return "следующий месяц"
    for pat, canon in _SEASON_TO_CANON:
        if pat.search(s):
            return canon
    dur = _extract_duration_months(s)
    if dur is not None:
        return dur
    if re.search(r"\bполгода\b|\bпол\s+года\b", s):
        return "полгода"
    if re.search(r"\bна\s+год\b", s):
        return "год"
    if _MAY_FORMS.search(s):
        return "май"
    for stem, canon in _MONTH_STEM_TO_CANON.items():
        if re.search(r"\b" + stem + r"[а-яё]*\b", s):
            return canon
    return None


def extract_category(text_lower: str, categories: dict[str, list[str]] | None = None) -> str | None:
    """Категория бизнеса клиента из словаря (каноническая форма) или None."""
    mapping = categories or PRODUCT_CATEGORY_DICT
    for canon, aliases in mapping.items():
        for alias in aliases:
            if alias in text_lower:
                return canon
    return None


def canonical_category(value: Any, categories: dict[str, list[str]] | None = None) -> str | None:
    """Строгая валидация категории по закрытому списку (точное совпадение).

    Каноника или алиас из конфига -> каноническая форма; всё остальное
    (галлюцинации модели вроде «суперсайт»/«formats», мусор) -> None.
    Подстроки НЕ принимаем: закрытый список должен оставаться закрытым.
    """
    if not isinstance(value, str):
        return None
    v = value.strip().lower().replace("ё", "е")
    if not v:
        return None
    mapping = categories or PRODUCT_CATEGORY_DICT
    for canon, aliases in mapping.items():
        forms = [canon, *(aliases or [])]
        if any(v == f.strip().lower().replace("ё", "е") for f in forms):
            return canon
    return None


def extract_cities(text_lower: str) -> list[str]:
    """Города из текста (тот же набор, что в rules-пути)."""
    cities = []
    city_patterns = {
        "Москва": r"\bмоскв[аеыуой]?\b",
        "Балашиха": r"\bбалаших[аеуойи]?\b",
        "Химки": r"\bхимк[а-я]{1,3}\b",
        "Мытищи": r"\bмытищ[а-я]{1,3}\b",
        "Подольск": r"\bподольск[а-я]{0,3}\b",
        "Одинцово": r"\bодинцов[оае]?\b",
        "Красногорск": r"\bкрасногорск[а-я]{0,3}\b",
        "Люберцы": r"\bлюберц[а-я]{1,3}\b",
        "Королёв": r"\bкорол[её]в[а-я]{0,3}\b",
    }
    for city_name, pat in city_patterns.items():
        if re.search(pat, text_lower):
            cities.append(city_name)
    return cities


def extract_formats(text_lower: str) -> list[str]:
    """Рекламные форматы из текста (тот же набор, что в rules-пути)."""
    formats = []
    for fmt in ["3×6", "щит", "суперсайт", "ситиборд", "экран", "медиафасад"]:
        if fmt.lower() in text_lower:
            formats.append(fmt)
    return formats


def extract_quantity(text_lower: str) -> int | None:
    """Количество (штуки/поверхности/стороны/щиты/экраны) или None.

    Число — цифрами или словом («два штук» → 2, эталон b008/b023).
    Без единицы количества («есть один», «один свободен», «сторона Б»,
    «пара вариантов») — None по ADR-021: ответы менеджера не считаются.
    """
    qty_match = re.search(
        r"\b(\d+|один|одна|одно|два|две|три|четыре|пять|шесть|семь|восемь|девять"
        r"|десять|одиннадцать|двенадцать)\s*"
        r"(щит[а-я]*|сторон[а-я]*|поверхност[а-я]*|экран[а-я]*|штук[а-я]*|шт\.?)\b",
        text_lower,
    )
    if qty_match:
        raw = qty_match.group(1)
        if raw.isdigit():
            try:
                return int(raw)
            except ValueError:
                return None
        return {"один": 1, "одна": 1, "одно": 1, "два": 2, "две": 2}.get(raw, NUMBER_WORDS.get(raw))
    return None


def backfill_entities_from_rules(
    entities: dict[str, Any],
    text: str,
    categories: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """П.4: добивка сущностей Qwen правилами-экстракторами на уровне полей.

    Пустые поля (None/[]) дополняются результатами правил-экстракторов
    по тому же тексту; непустые значения модели НЕ перезаписываются.
    Категория валидируется по закрытому списку из конфига: чужое значение
    обнуляется и тоже добивается правилами (или остаётся null).
    """
    if not isinstance(entities, dict):
        entities = {}
    normalizer = get_normalizer()
    text_lower = normalizer.normalize(text).lower()

    if not entities.get("cities"):
        found = extract_cities(text_lower)
        if found:
            entities["cities"] = found
    if not entities.get("formats"):
        found = extract_formats(text_lower)
        if found:
            entities["formats"] = found
    if entities.get("quantity") is None:
        found = extract_quantity(text_lower)
        if found is not None:
            entities["quantity"] = found
    if entities.get("target_period") is None:
        found = extract_period(text_lower)
        if found is not None:
            entities["target_period"] = found
    if entities.get("budget_rub") is None:
        found = extract_budget_rub(text_lower)
        if found is not None:
            entities["budget_rub"] = found
    valid = canonical_category(entities.get("product_category"), categories)
    if valid is None:
        found = extract_category(text_lower, categories)
        entities["product_category"] = found
    else:
        entities["product_category"] = valid
    return entities


def extract_budget_rub(text_lower: str) -> float | int | None:
    """Бюджет в рублях: число+валюта, сгруппированные цифры, текстовые суммы.

    Примеры: «200 000 руб.» → 200000; «полмиллиона рублей» → 500000;
    «миллион рублей» → 1000000; «на двести тысяч» (без валюты) → 200000.
    Голые малые числа без валюты/множителя бюджетом не считаются.
    """
    normed = get_normalizer().normalize(text_lower).lower()
    m = re.search(
        r"(\d[\d\s]*\d|\d)\s*(миллиард[а-я]*|миллион[а-я]*|млрд\.?|млн\.?|тысяч[а-я]*|тыс\.?|руб\.?)",
        normed,
    )
    if m:
        num = re.sub(r"\s+", "", m.group(1))
        try:
            base = float(num.replace(",", "."))
        except ValueError:
            base = None
        if base is not None:
            unit = m.group(2)
            if re.match(r"миллиард|млрд", unit):
                base *= 1_000_000_000
            elif re.match(r"миллион|млн", unit):
                base *= 1_000_000
            elif re.match(r"тысяч|тыс", unit):
                base *= 1_000
            return int(base) if float(base).is_integer() else base
    m = re.search(r"\b\d{1,3}(?:\s\d{3})+\b", normed)
    if m:
        # Год («две тысячи четвертый год» → «2 000 четвертый год») — не бюджет:
        # тысячи + порядковое/год в исходном тексте отменяют ветку групповых цифр
        # (иначе чинка склеек нормалайзера дала бы FP 2000 на Golos-294).
        if not re.search(
            r"тысяч[а-я]*\s+(перв|втор|трет|четверт|пят|шест|седьм|восьм|девят|десят"
            r"|одиннадцат|двенадцат|тринадцат|четырнадцат|пятнадцат|шестнадцат"
            r"|семнадцат|восемнадцат|девятнадцат|двадцат|тридцат|год\b)",
            text_lower,
        ):
            try:
                return int(re.sub(r"\s+", "", m.group(0)))
            except ValueError:
                pass
    for pat in (
        r"пол[уы]?\s*миллиард[а-я]*",
        r"пол[уы]?\s*миллион[а-я]*",
        r"полутора\s*миллион[а-я]*",
        r"\bмиллиард[а-я]*",
        r"\bмиллион[а-я]*",
    ):
        if re.search(pat, text_lower):
            val = _parse_scaled_amount(re.search(pat, text_lower).group(0))  # type: ignore[union-attr]
            if val is not None:
                return val
            if "миллиард" in pat:
                return 1_000_000_000
            return 1_000_000
    return None
