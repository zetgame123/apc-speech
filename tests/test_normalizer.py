import pytest
from app.asr.normalizer import RussianTextNormalizer


def test_billboard_dimensions_normalization():
    normalizer = RussianTextNormalizer()

    assert normalizer.normalize("нам нужен щит три на шесть в Балашихе") == "нам нужен щит 3×6 в Балашихе"
    assert normalizer.normalize("суперсайт шесть на три свободен") == "суперсайт 6×3 свободен"
    assert normalizer.normalize("конструкция 3х6 или 3*6") == "конструкция 3×6 или 3×6"


def test_time_normalization():
    normalizer = RussianTextNormalizer()

    assert normalizer.normalize("ролик на пятнадцать секунд") == "ролик на 15 с"
    assert normalizer.normalize("хронометраж двадцать секунд") == "хронометраж 20 с"
    assert normalizer.normalize("размещение на два месяца") == "размещение на 2 мес."


def test_currency_normalization():
    normalizer = RussianTextNormalizer()

    assert normalizer.normalize("бюджет сто тысяч рублей") == "бюджет 100 000 руб."
    assert normalizer.normalize("стоимость 50000 рублей") == "стоимость 50000 руб."
