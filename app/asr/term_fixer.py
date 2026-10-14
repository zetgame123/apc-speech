"""Дисциплинированная поправка терминов: только явные пары или OOV-слова.

Правила (п.4):
- явная замена — только если слово (в нижнем регистре, ё→е) есть в таблице
  CONFUSION_PAIRS, построенной на половине A фраз TTS-20
  (скрипт scripts/build_termfix.py, половина B при построении не использовалась);
- нечёткая замена — только для слов, которых НЕТ в словаре Natasha/navec
  (обычные слова вроде «привет», «джой», «сбер» защищены), при сходстве
  difflib >= порога с однословным термином из config/vocabulary_hints.yaml.
"""

import difflib
import re

# Замороженная таблица с половины A (term_00-04 + term_10-14, podlodka base).
# Происхождение каждой пары — scripts/build_termfix.py, reports/termfix.json.
CONFUSION_PAIRS: dict[str, str] = {
    "billboard": "билборд",
    "одинцова": "Одинцово",
}

FUZZY_THRESHOLD = 0.8

_STRIP_RE = re.compile(r"^([^\w]+)?(.*?)([^\w]+)?$")


def _split_affixes(word: str) -> tuple[str, str, str]:
    """Отделяет пунктуацию по краям (ядро — для словаря, аффиксы — вернуть)."""
    m = _STRIP_RE.match(word)
    if not m:
        return "", word, ""
    return m.group(1) or "", m.group(2), m.group(3) or ""


def _load_terms() -> list[str]:
    import yaml

    terms: set[str] = set()
    with open("config/vocabulary_hints.yaml", encoding="utf-8") as f:
        hints = yaml.safe_load(f)
    for value in hints.values():
        if isinstance(value, list):
            for term in value:
                norm = str(term).lower().replace("ё", "е").strip()
                if norm and " " not in norm:
                    terms.add(norm)
    return sorted(terms)


def _load_vocab():
    try:
        from natasha import NewsEmbedding

        return NewsEmbedding().vocab
    except Exception:
        return None


def _match_case(surface: str, template: str) -> str:
    if template and template[0].isupper():
        return surface[:1].upper() + surface[1:]
    return surface


def apply_term_fixes(
    text: str,
    pairs: dict[str, str] | None = None,
    threshold: float = FUZZY_THRESHOLD,
    vocab=None,
    terms: list[str] | None = None,
) -> str:
    """Применяет явные пары + OOV-нечёткую замену. Чистая функция (тестируемо)."""
    if not text:
        return text
    pairs = CONFUSION_PAIRS if pairs is None else pairs
    if vocab is None:
        vocab = _load_vocab()
    if terms is None:
        terms = _load_terms()
    exact = set(terms)
    out = []
    for word in text.split():
        pre, core, post = _split_affixes(word)
        if not core:
            out.append(word)
            continue
        low = core.lower().replace("ё", "е")
        if low in pairs:
            out.append(pre + _match_case(pairs[low], core) + post)
            continue
        if low in exact:
            out.append(word)
            continue
        if vocab is not None:
            try:
                if low in vocab:
                    out.append(word)
                    continue
            except Exception:
                pass
        best, best_ratio = None, 0.0
        for term in terms:
            if abs(len(term) - len(low)) > 2:
                continue
            ratio = difflib.SequenceMatcher(None, low, term).ratio()
            if ratio > best_ratio:
                best, best_ratio = term, ratio
        if best is not None and best_ratio >= threshold:
            out.append(pre + _match_case(best, core) + post)
        else:
            out.append(word)
    return " ".join(out)
