"""Юнит-тесты поправки терминов: явные пары, OOV-защита, пунктуация."""

from app.asr.term_fixer import _split_affixes, apply_term_fixes


class _Vocab:
    def __init__(self, words):
        self._words = set(words)

    def __contains__(self, word):
        return word in self._words


VOCAB = _Vocab(["привет", "джой", "сбер", "красногорске", "люблина", "мир"])
TERMS = ["билборд", "пиллар", "щит", "ситиборд"]


def test_explicit_pair_case_insensitive():
    assert apply_term_fixes("аренда Billboard", vocab=VOCAB, terms=TERMS) == "аренда Билборд"


def test_explicit_pair_with_punctuation():
    out = apply_term_fixes(
        "цена на Одинцова?", vocab=VOCAB, terms=TERMS, pairs={"одинцова": "Одинцово"}
    )
    assert out == "цена на Одинцово?"


def test_oov_distortion_fixed():
    assert apply_term_fixes("свободен пилвар", vocab=VOCAB, terms=TERMS) == "свободен пиллар"


def test_in_vocab_word_protected():
    assert apply_term_fixes("привет джой", vocab=VOCAB, terms=TERMS) == "привет джой"


def test_in_vocab_inflection_with_period_protected():
    # Регрессия п.4: пунктуация не должна обходить vocab-защиту.
    assert (
        apply_term_fixes("занятость в Красногорске.", vocab=VOCAB, terms=TERMS)
        == "занятость в Красногорске."
    )


def test_exact_term_untouched():
    assert apply_term_fixes("аренда билборд.", vocab=VOCAB, terms=TERMS) == "аренда билборд."


def test_empty_and_no_terms():
    assert apply_term_fixes("") == ""
    assert apply_term_fixes("привет мир", vocab=VOCAB, terms=TERMS) == "привет мир"


def test_split_affixes():
    assert _split_affixes("«суперсайт»") == ("«", "суперсайт", "»")
    assert _split_affixes("слово") == ("", "слово", "")
    assert _split_affixes("...") == ("...", "", "")
