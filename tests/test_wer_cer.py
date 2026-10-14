import pytest

from evaluation.wer_cer import (
    align_words,
    calculate_wer_cer,
    calculate_wer_cer_with,
    normalize_eval_text,
    normalize_lexical,
    normalize_strict,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Привет, Мир!", "привет мир"),  # регистр + пунктуация
        ("Ёжик ест ещё", "ежик ест еще"),  # ё/е
        ("три на шесть", "3×6"),  # числительные со склонением
        ("пятнадцать секунд", "15 с"),
        ("двести тысяч рублей", "200 000 руб"),
        ("", ""),
        ("!!!", ""),
    ],
)
def test_normalize_eval_text_known_values(raw, expected):
    assert normalize_eval_text(raw) == expected


def test_wer_cer_identical_is_zero():
    wer, cer = calculate_wer_cer("щиты три на шесть", "Щиты 3х6!")
    assert wer == 0.0
    assert cer == 0.0


def test_wer_cer_counts_word_errors():
    wer, cer = calculate_wer_cer("одно два три четыре", "одно два пять четыре")
    assert wer == 0.25
    assert 0.0 < cer < 1.0


def test_wer_empty_reference_is_zero():
    wer, cer = calculate_wer_cer("", "что-то")
    assert wer == 0.0
    assert cer == 0.0


def test_normalize_variants_known_values():
    # Лексический вариант без ITN: числа словами не сворачиваются.
    assert normalize_lexical("Привет, Мир!") == "привет мир"
    assert normalize_lexical("три на шесть") == "три на шесть"
    # Строгий вариант: ё и дефисы сохраняются, ITN работает.
    assert "ё" in normalize_strict("Ёжик")
    assert normalize_strict("что-нибудь") == "что-нибудь"
    assert normalize_strict("три на шесть") == "3×6"


def test_wer_with_variant_lexical_penalizes_digits():
    wer_lex, _ = calculate_wer_cer_with("пять тысяч рублей", "5 000 руб", normalize_lexical)
    wer_def, _ = calculate_wer_cer_with("пять тысяч рублей", "5 000 руб", normalize_eval_text)
    assert wer_lex > wer_def


def test_align_words_counts_ops():
    ops = align_words(["одно", "два", "три"], ["одно", "пять", "три", "четыре"])
    kinds = [o[0] for o in ops]
    assert kinds.count("ok") == 2
    assert kinds.count("sub") == 1
    assert kinds.count("ins") == 1
    assert "del" not in kinds
