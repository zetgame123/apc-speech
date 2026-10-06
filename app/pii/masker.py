import os
import re
from typing import Any

import yaml
from natasha import Doc, NewsEmbedding, NewsNERTagger, Segmenter

# Regex patterns for sensitive PII
PHONE_REGEX = re.compile(
    r"(?:\+7|8)[\s\-\.\(]*(\d{3})[\s\-\.\)]*(\d{3})[\s\-\.]*(\d{2})[\s\-\.]*(\d{2})|"
    r"\b\d{3}[-\s]\d{3}[-\s]\d{4}\b|"
    r"\b\d{10,11}\b"
)

SPOKEN_PHONE_PATTERN = re.compile(
    r"\b(плюс\s+семь|восемь)\s+"
    r"((?:девятьсот|восемьсот|семьсот|шестьсот|пятьсот|четыреста|триста|двести|сто|\d+)(?:\s+(?:[а-я]+|\d+)){0,2})\s+"
    r"((?:девятьсот|восемьсот|семьсот|шестьсот|пятьсот|четыреста|триста|двести|сто|\d+)(?:\s+(?:[а-я]+|\d+)){0,2})\s+"
    r"((?:девяносто|восемьдесят|семьдесят|шестьдесят|пятьдесят|сорок|тридцать|двадцать|\d+)(?:\s+(?:[а-я]+|\d+))?)\s+"
    r"((?:девяносто|восемьдесят|семьдесят|шестьдесят|пятьдесят|сорок|тридцать|двадцать|\d+)(?:\s+(?:[а-я]+|\d+))?)\b",
    re.IGNORECASE,
)

NUMBER_WORDS = {
    "ноль": 0,
    "один": 1,
    "одна": 1,
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
    "тринадцать": 13,
    "четырнадцать": 14,
    "пятнадцать": 15,
    "шестнадцать": 16,
    "семнадцать": 17,
    "восемнадцать": 18,
    "девятнадцать": 19,
    "двадцать": 20,
    "тридцать": 30,
    "сорок": 40,
    "пятьдесят": 50,
    "шестьдесят": 60,
    "семьдесят": 70,
    "восемьдесят": 80,
    "девяносто": 90,
    "сто": 100,
    "двести": 200,
    "триста": 300,
    "четыреста": 400,
    "пятьсот": 500,
    "шестьсот": 600,
    "семьсот": 700,
    "восемьсот": 800,
    "девятьсот": 900,
}


HUNDREDS_WORDS = {
    "сто": 100,
    "двести": 200,
    "триста": 300,
    "четыреста": 400,
    "пятьсот": 500,
    "шестьсот": 600,
    "семьсот": 700,
    "восемьсот": 800,
    "девятьсот": 900,
}
TENS_WORDS = {
    "двадцать": 20,
    "тридцать": 30,
    "сорок": 40,
    "пятьдесят": 50,
    "шестьдесят": 60,
    "семьдесят": 70,
    "восемьдесят": 80,
    "девяносто": 90,
}
TEENS_WORDS = {
    "десять": 10,
    "одиннадцать": 11,
    "двенадцать": 12,
    "тринадцать": 13,
    "четырнадцать": 14,
    "пятнадцать": 15,
    "шестнадцать": 16,
    "семнадцать": 17,
    "восемнадцать": 18,
    "девятнадцать": 19,
}
UNITS_WORDS = {
    "ноль": 0,
    "нуль": 0,
    "один": 1,
    "одна": 1,
    "два": 2,
    "две": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
}

ALL_NUM_WORDS = (
    set(HUNDREDS_WORDS)
    | set(TENS_WORDS)
    | set(TEENS_WORDS)
    | set(UNITS_WORDS)
    | {"плюс", "+7", "8", "7"}
)


def convert_spoken_phones(text: str) -> str:
    """Parses arbitrary spoken Russian phone number constructions into digits."""
    tokens = list(re.finditer(r"(\+?\d+|[а-яёА-ЯЁ]+)", text))
    if not tokens:
        return text

    i = 0
    replacements = []
    while i < len(tokens):
        w = tokens[i].group(0).lower()
        if w in ALL_NUM_WORDS or w.isdigit() or w == "+7":
            start_idx = i
            digits = []
            prefix = ""
            curr = i

            # Check for leading 'плюс семь' or 'плюс 7'
            if (
                curr + 1 < len(tokens)
                and tokens[curr].group(0).lower() == "плюс"
                and tokens[curr + 1].group(0).lower() in ("семь", "7")
            ):
                prefix = "+7"
                curr += 2
            elif w == "+7":
                prefix = "+7"
                curr += 1
            elif w in ("восемь", "8"):
                prefix = "8"
                curr += 1
            elif w in ("семь", "7"):
                prefix = "7"
                curr += 1

            # Parse remaining tokens in sequence
            while curr < len(tokens):
                t_raw = tokens[curr].group(0).lower()
                if t_raw.isdigit():
                    digits.append(t_raw)
                    curr += 1
                elif t_raw in HUNDREDS_WORDS:
                    val = HUNDREDS_WORDS[t_raw]
                    curr += 1
                    if curr < len(tokens) and tokens[curr].group(0).lower() in TENS_WORDS:
                        val += TENS_WORDS[tokens[curr].group(0).lower()]
                        curr += 1
                        if curr < len(tokens) and tokens[curr].group(0).lower() in UNITS_WORDS:
                            val += UNITS_WORDS[tokens[curr].group(0).lower()]
                            curr += 1
                    elif curr < len(tokens) and tokens[curr].group(0).lower() in TEENS_WORDS:
                        val += TEENS_WORDS[tokens[curr].group(0).lower()]
                        curr += 1
                    elif curr < len(tokens) and tokens[curr].group(0).lower() in UNITS_WORDS:
                        val += UNITS_WORDS[tokens[curr].group(0).lower()]
                        curr += 1
                    digits.append(f"{val:03d}")
                elif t_raw in TENS_WORDS:
                    val = TENS_WORDS[t_raw]
                    curr += 1
                    if curr < len(tokens) and tokens[curr].group(0).lower() in UNITS_WORDS:
                        val += UNITS_WORDS[tokens[curr].group(0).lower()]
                        curr += 1
                    digits.append(f"{val:02d}")
                elif t_raw in TEENS_WORDS:
                    digits.append(str(TEENS_WORDS[t_raw]))
                    curr += 1
                elif t_raw in UNITS_WORDS:
                    digits.append(str(UNITS_WORDS[t_raw]))
                    curr += 1
                else:
                    break

            digit_str = "".join(digits)
            raw_digits_only = re.sub(r"\D", "", prefix + digit_str)
            total_digits = len(raw_digits_only)

            if total_digits == 11 and (
                prefix in ("+7", "8", "7") or (prefix == "" and digit_str.startswith(("7", "8")))
            ):
                full_num = (prefix if prefix else "") + digit_str
                span_start = tokens[start_idx].start()
                span_end = tokens[curr - 1].end()
                replacements.append((span_start, span_end, full_num))
                i = curr
                continue
            elif total_digits == 10 and digit_str.startswith("9"):
                full_num = "+7" + digit_str
                span_start = tokens[start_idx].start()
                span_end = tokens[curr - 1].end()
                replacements.append((span_start, span_end, full_num))
                i = curr
                continue
        i += 1

    res = text
    for start, end, rep in reversed(replacements):
        res = res[:start] + rep + res[end:]
    return res


def _parse_num_group(g: str, length: int) -> str:
    parts = g.strip().split()
    total = 0
    for p in parts:
        if p.isdigit():
            total += int(p)
        elif p.lower() in NUMBER_WORDS:
            total += NUMBER_WORDS[p.lower()]
    return str(total).zfill(length)


def _spoken_phone_repl(m: re.Match) -> str:
    prefix = "+7" if "плюс" in m.group(1).lower() or "семь" in m.group(1).lower() else "8"
    g1 = _parse_num_group(m.group(2), 3)
    g2 = _parse_num_group(m.group(3), 3)
    g3 = _parse_num_group(m.group(4), 2)
    g4 = _parse_num_group(m.group(5), 2)
    return f"{prefix} {g1} {g2} {g3} {g4}"


EMAIL_REGEX = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")

BANK_CARD_REGEX = re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b")

PASSPORT_RF_REGEX = re.compile(r"\b\d{2}\s?\d{2}\s+\d{6}\b")


# --- Natasha NER (lazy-loaded singleton) ---
# Распознаёт ФИО (тип сущности PER) в расшифровке звонка для их маскирования.
# Модели грузятся один раз на процесс (ASR и анализ выполняются локально на сервере,
# без обращения к сети), чтобы не увеличивать латентность каждого вызова mask_text().
_natasha_segmenter: Segmenter | None = None
_natasha_ner_tagger: NewsNERTagger | None = None


def _get_natasha_ner():
    global _natasha_segmenter, _natasha_ner_tagger
    if _natasha_ner_tagger is None:
        _natasha_segmenter = Segmenter()
        embedding = NewsEmbedding()
        _natasha_ner_tagger = NewsNERTagger(embedding)
    return _natasha_segmenter, _natasha_ner_tagger


# --- Domain-term allowlist (false-positive guard for Natasha NER) ---
# NewsNERTagger обучен на новостном корпусе и регулярно помечает капитализированные
# доменные термины/топонимы отрасли наружной рекламы (например, "Ситиборды" в начале
# предложения, "Балашиха", "Павелецкая") как сущность PER. Чтобы не терять эти слова
# из расшифровки, спаны NER, совпадающие (по нормализованной форме) с терминами из
# config/vocabulary_hints.yaml (тот же словарь, что используется ASR-хотвордами в
# app/asr/engine.py), не считаются именами и не маскируются.
_DOMAIN_TERM_ALLOWLIST: set[str] | None = None


def _load_domain_term_allowlist() -> set[str]:
    global _DOMAIN_TERM_ALLOWLIST
    if _DOMAIN_TERM_ALLOWLIST is not None:
        return _DOMAIN_TERM_ALLOWLIST

    allowlist: set[str] = set()
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    hints_file = os.path.join(base_dir, "config", "vocabulary_hints.yaml")
    if os.path.exists(hints_file):
        try:
            with open(hints_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            for key in ("domain_terms", "metro_stations", "highways", "cities"):
                for term in data.get(key, []) or []:
                    allowlist.add(str(term).strip().lower())
        except Exception:
            pass

    _DOMAIN_TERM_ALLOWLIST = allowlist
    return allowlist


def _is_domain_term(span_text: str) -> bool:
    """
    Returns True if every word of span_text matches a known domain term/toponym
    (e.g. "Ситиборды", "Павелецкой", "Балашиха"), so it should NOT be treated as
    a leaked person name even if Natasha NER tagged it as PER.
    """
    allowlist = _load_domain_term_allowlist()
    if not allowlist:
        return False
    words = span_text.strip().lower().split()
    if not words:
        return False
    return all(w in allowlist for w in words)


def mask_person_names(text: str, counter_state: dict[str, Any] | None = None) -> str:
    """
    Uses Natasha's NewsNERTagger to find person-name (PER) spans and replaces them
    with consistently-numbered [ИМЯ_N] placeholders, mirroring the phone-masking scheme.
    counter_state (optional) keeps numbering consistent across repeated calls, e.g.
    {"counter": 0, "cache": {}}.

    """
    if not text or not text.strip():
        return text

    segmenter, ner_tagger = _get_natasha_ner()
    doc = Doc(text)
    doc.segment(segmenter)
    doc.tag_ner(ner_tagger)

    person_spans = [
        span
        for span in doc.spans
        if span.type == "PER" and not _is_domain_term(text[span.start : span.stop])
    ]
    if not person_spans:
        return text

    state = counter_state if counter_state is not None else {"counter": 0, "cache": {}}
    state.setdefault("counter", 0)
    state.setdefault("cache", {})

    result = text
    for span in sorted(person_spans, key=lambda s: s.start, reverse=True):
        name_raw = text[span.start : span.stop]
        name_key = name_raw.lower().strip()
        if name_key not in state["cache"]:
            state["counter"] += 1
            state["cache"][name_key] = f"[ИМЯ_{state['counter']}]"
        result = result[: span.start] + state["cache"][name_key] + result[span.stop :]

    return result


def has_unmasked_person_name(text: str) -> bool:
    """
    Runs Natasha NER over `text` and returns True if any person-name (PER)
    span is still present (i.e. the text was not passed through
    mask_person_names, or NER finds a name the masking step missed).
    Used by assert_no_leaked_pii() as a safety net before sending text to
    any external adapter (e.g. the optional LLM port, ADR-004).
    """
    if not text or not text.strip():
        return False

    segmenter, ner_tagger = _get_natasha_ner()
    doc = Doc(text)
    doc.segment(segmenter)
    doc.tag_ner(ner_tagger)

    return any(
        span.type == "PER" and not _is_domain_term(text[span.start : span.stop])
        for span in doc.spans
    )


class PiiMasker:
    def __init__(self):
        self.phone_counter = 0
        self.phone_cache: dict[str, str] = {}
        self.name_state: dict[str, Any] = {"counter": 0, "cache": {}}

    def mask_text(self, text: str) -> str:
        """
        Replaces sensitive personally identifiable information (PII) with safe placeholders.
        """
        if not text:
            return ""

        masked = text

        # 1. Mask bank cards first (to avoid conflict with phones)
        masked = BANK_CARD_REGEX.sub("[КАРТА]", masked)

        # 2. Mask passports
        masked = PASSPORT_RF_REGEX.sub("[ПАСПОРТ]", masked)

        # 3. Mask email addresses
        masked = EMAIL_REGEX.sub("[EMAIL]", masked)

        # 4. Convert spoken phone numbers to standard format
        masked = convert_spoken_phones(masked)
        masked = SPOKEN_PHONE_PATTERN.sub(_spoken_phone_repl, masked)

        # 5. Mask phone numbers with consistent numbering
        def replace_phone(match):
            raw = match.group(0)
            digits = re.sub(r"\D", "", raw)
            if len(digits) >= 10:
                if digits not in self.phone_cache:
                    self.phone_counter += 1
                    self.phone_cache[digits] = f"[ТЕЛЕФОН_{self.phone_counter}]"
                return self.phone_cache[digits]
            return raw

        masked = PHONE_REGEX.sub(replace_phone, masked)

        # 6. Mask person names (ФИО) via Natasha NER (NewsNERTagger), consistently numbered
        # like phones, e.g. "Иван Петров" -> "[ИМЯ_1]". Covers brief.md "маскирование ... в
        # расшифровке" beyond phones/email/cards/passports (common-requirements.md п.6).
        masked = mask_person_names(masked, counter_state=self.name_state)

        return masked

    mask = mask_text

    def assert_no_leaked_pii(self, text: str) -> bool:
        """
        Safety check before passing text to any external adapter.
        Raises ValueError if unmasked phones or cards are detected.
        """
        if BANK_CARD_REGEX.search(text):
            raise ValueError("PII Leak detected: Unmasked bank card found in text!")
        if EMAIL_REGEX.search(text):
            raise ValueError("PII Leak detected: Unmasked email found in text!")
        if PASSPORT_RF_REGEX.search(text):
            raise ValueError("PII Leak detected: Unmasked passport found in text!")
        if PHONE_REGEX.search(text) or SPOKEN_PHONE_PATTERN.search(text):
            raise ValueError("PII Leak detected: Unmasked phone found in text!")
        if has_unmasked_person_name(text):
            raise ValueError("PII Leak detected: Unmasked person name found in text!")
        return True
