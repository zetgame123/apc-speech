import re

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


class PiiMasker:
    def __init__(self):
        self.phone_counter = 0
        self.phone_cache: dict[str, str] = {}

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
        return True
