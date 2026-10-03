import re

# Regex patterns for sensitive PII
PHONE_REGEX = re.compile(
    r"(?:\+7|8)[\s\-\(]*(\d{3})[\s\-\)]*(\d{3})[\s\-]*(\d{2})[\s\-]*(\d{2})|"
    r"\b\d{3}[-\s]\d{3}[-\s]\d{4}\b|"
    r"\b\d{10,11}\b"
)

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

        # 4. Mask phone numbers with consistent numbering
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
        if PHONE_REGEX.search(text):
            raise ValueError("PII Leak detected: Unmasked phone found in text!")
        return True
