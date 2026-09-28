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

        # Currencies & Percentages
        self.currency_patterns = [
            (re.compile(r"\bсто\s+тысяч\s+рублей\b", re.IGNORECASE), "100 000 руб."),
            (re.compile(r"\bдвести\s+тысяч\s+рублей\b", re.IGNORECASE), "200 000 руб."),
            (re.compile(r"\bпятьдесят\s+тысяч\s+рублей\b", re.IGNORECASE), "50 000 руб."),
            (re.compile(r"\b(\d+)\s+тысяч\s+рублей\b", re.IGNORECASE), r"\1 000 руб."),
            (re.compile(r"\b(\d+)\s+рублей\b", re.IGNORECASE), r"\1 руб."),
            (re.compile(r"\b(\d+)\s+рубля\b", re.IGNORECASE), r"\1 руб."),
            (re.compile(r"\b(\d+)\s+рубль\b", re.IGNORECASE), r"\1 руб."),
            (re.compile(r"\bдвадцать\s+процентов\b", re.IGNORECASE), "20%"),
            (re.compile(r"\bдесять\s+процентов\b", re.IGNORECASE), "10%"),
            (re.compile(r"\b(\d+)\s+процент(а|ов)?\b", re.IGNORECASE), r"\1%"),
        ]

        # Dates
        self.date_patterns = [
            (re.compile(r"\bпервое\s+октября\b", re.IGNORECASE), "1 октября"),
            (re.compile(r"\bдвадцать\s+шестой\s+год\b", re.IGNORECASE), "2026 г."),
            (re.compile(r"\bс\s+мая\s+по\s+сентябрь\b", re.IGNORECASE), "с мая по сентябрь"),
        ]
        
        # Phone
        self.phone_patterns = [
            (re.compile(r"\bплюс\s+семь\s+девятьсот\s+двадцать\s+шесть\b", re.IGNORECASE), "+7 926"),
        ]

        # Domain acoustic homophones
        self.homophone_patterns = [
            (re.compile(r"\bсчет(а|ов|ам|ами|ах)?\s+(3×6|6×3|3х6|6х3|три на шесть|шесть на три|ситиборд|билборд)", re.IGNORECASE), r"щит\1 \2"),
            (re.compile(r"\b(стоимость|цена|аренда|размещение|печать|монтаж|бронь)\s+счетов\b", re.IGNORECASE), r"\1 щитов"),
            (re.compile(r"\bсчетов\s+в\s+([А-Яа-яA-Za-z]+)", re.IGNORECASE), r"щитов в \1"),
        ]

    def normalize(self, text: str) -> str:
        """Applies rule-based Inverse Text Normalization to Russian speech text."""
        if not text:
            return ""

        res = text

        for pattern, replacement in self.dimension_patterns:
            res = pattern.sub(replacement, res)

        for pattern, replacement in self.currency_patterns:
            res = pattern.sub(replacement, res)
            
        for pattern, replacement in self.date_patterns:
            res = pattern.sub(replacement, res)
            
        for pattern, replacement in self.phone_patterns:
            res = pattern.sub(replacement, res)

        for pattern, replacement in self.homophone_patterns:
            res = pattern.sub(replacement, res)

        for pattern, replacement in self.time_patterns:
            res = pattern.sub(replacement, res)

        res = re.sub(r"\s+", " ", res).strip()

        return res
