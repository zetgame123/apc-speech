import pytest

from evaluation.wer_cer import calculate_wer_cer, normalize_eval_text


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
