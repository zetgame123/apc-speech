import re

# Word to digit mapping for Russian numbers
NUMBER_WORDS = {
    "один": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4,
    "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14,
    "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18,
    "девятнадцать": 19, "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50,
    "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80, "девяносто": 90,
    "сто": 100, "двести": 200, "триста": 300, "четыреста": 400, "пятьсот": 500,
    "шестьсот": 600, "семьсот": 700, "восемьсот": 800, "девятьсот": 900,
}


class RussianTextNormalizer:
    def __init__(self):
        # Patterns for outdoor billboard dimensions
        self.dimension_patterns = [
            (re.compile(r"\bтри\s+на\s+шесть\b", re.IGNORECASE), "3×6"),
            (re.compile(r"\bшесть\s+на\s+три\b", re.IGNORECASE), "6×3"),
            (re.compile(r"\bтри\s+х\s+шесть\b", re.IGNORECASE), "3×6"),
            (re.compile(r"\bшесть\s+х\s+три\b", re.IGNORECASE), "6×3"),
            (re.compile(r"\b3\s*[xхXХ*]\s*6\b"), "3×6"),
            (re.compile(r"\b6\s*[xхXХ*]\s*3\b"), "6×3"),
            (re.compile(r"\bпять\s+на\s+двенадцать\b", re.IGNORECASE), "5×12"),
            (re.compile(r"\bчетыре\s+на\s+двенадцать\b", re.IGNORECASE), "4×12"),
        ]

        # Time units
        self.time_patterns = [
            (re.compile(r"\b(\d+)\s+секунд[ыа]?\b", re.IGNORECASE), r"\1 с"),
            (re.compile(r"\bпятнадцать\s+секунд\b", re.IGNORECASE), "15 с"),
            (re.compile(r"\bдвадцать\s+секунд\b", re.IGNORECASE), "20 с"),
            (re.compile(r"\bдесять\s+секунд\b", re.IGNORECASE), "10 с"),
            (re.compile(r"\b(\d+)\s+минут[ыа]?\b", re.IGNORECASE), r"\1 мин"),
            (re.compile(r"\bдва\s+месяца\b", re.IGNORECASE), "2 мес."),
            (re.compile(r"\bтри\s+месяца\b", re.IGNORECASE), "3 мес."),
            (re.compile(r"\bодин\s+месяц\b", re.IGNORECASE), "1 мес."),
            (re.compile(r"\b(\d+)\s+месяц(ев|а)?\b", re.IGNORECASE), r"\1 мес."),
        ]

        # Currencies
        self.currency_patterns = [
            (re.compile(r"\bсто\s+тысяч\s+рублей\b", re.IGNORECASE), "100 000 руб."),
            (re.compile(r"\bдвести\s+тысяч\s+рублей\b", re.IGNORECASE), "200 000 руб."),
            (re.compile(r"\bпятьдесят\s+тысяч\s+рублей\b", re.IGNORECASE), "50 000 руб."),
            (re.compile(r"\b(\d+)\s+тысяч\s+рублей\b", re.IGNORECASE), r"\1 000 руб."),
            (re.compile(r"\b(\d+)\s+рублей\b", re.IGNORECASE), r"\1 руб."),
            (re.compile(r"\b(\d+)\s+рубля\b", re.IGNORECASE), r"\1 руб."),
            (re.compile(r"\b(\d+)\s+рубль\b", re.IGNORECASE), r"\1 руб."),
        ]

    def normalize(self, text: str) -> str:
        """Applies rule-based Inverse Text Normalization to Russian speech text."""
        if not text:
            return ""

        res = text

        # 1. Billboard sizes
        for pattern, replacement in self.dimension_patterns:
            res = pattern.sub(replacement, res)

        # 2. Currencies
        for pattern, replacement in self.currency_patterns:
            res = pattern.sub(replacement, res)

        # 3. Time units
        for pattern, replacement in self.time_patterns:
            res = pattern.sub(replacement, res)

        # 4. Collapse extra spaces
        res = re.sub(r"\s+", " ", res).strip()

        return res
