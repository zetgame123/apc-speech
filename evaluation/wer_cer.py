import re

from app.asr.normalizer import RussianTextNormalizer

normalizer = RussianTextNormalizer()


def normalize_eval_text(text: str) -> str:
    """Applies Russian ITN, lowercases, strips punctuation, and collapses whitespaces."""
    if not text:
        return ""
    # 1. Apply ITN normalizer (converts "три на шесть" to "3×6", numbers, etc.)
    t = normalizer.normalize(text)
    # 2. Lowercase, fold ё->е (орфографический вариант, не фонема), and remove
    # punctuation except standard word characters. Fold здесь, а не в
    # продуктовом нормалайзере: видимый текст хранит ё.
    t = re.sub(r"[^\w\s×]", " ", t.lower().replace("ё", "е"))
    # 3. Collapse multiple spaces
    t = re.sub(r"\s+", " ", t).strip()
    return t


def normalize_lexical(text: str) -> str:
    """Вариант Б (лексический, без ITN): регистр, ё/е, пунктуация — без чисел словами."""
    if not text:
        return ""
    t = re.sub(r"[^\w\s]", " ", text.lower().replace("ё", "е"))
    t = re.sub(r"\s+", " ", t).strip()
    return t


def normalize_strict(text: str) -> str:
    """Вариант В (строгий): ITN + регистр и пунктуация, но ё и дефисы сохраняются."""
    if not text:
        return ""
    t = normalizer.normalize(text)
    t = t.lower()
    t = re.sub(r"[^\w\s×-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


NORMALIZATIONS = {
    "default": normalize_eval_text,
    "lexical": normalize_lexical,
    "strict": normalize_strict,
}


def calculate_wer_cer_with(reference: str, hypothesis: str, normalize_fn) -> tuple[float, float]:
    """WER/CER с заданной нормализацией (для аудита вариантов)."""
    ref_norm = normalize_fn(reference)
    hyp_norm = normalize_fn(hypothesis)
    ref_words = ref_norm.split()
    hyp_words = hyp_norm.split()
    d_words = levenshtein_distance(ref_words, hyp_words)
    wer = d_words / len(ref_words) if ref_words else 0.0
    d_chars = levenshtein_distance(list(ref_norm), list(hyp_norm))
    cer = d_chars / len(ref_norm) if ref_norm else 0.0
    return round(wer, 4), round(cer, 4)


def calculate_wer_cer(reference: str, hypothesis: str) -> tuple[float, float]:
    """Computes Word Error Rate (WER) and Character Error Rate (CER)."""
    return calculate_wer_cer_with(reference, hypothesis, normalize_eval_text)


def align_words(
    ref_words: list[str], hyp_words: list[str]
) -> list[tuple[str, str | None, str | None]]:
    """Выравнивание слов (sub/ins/del/ok) обратным проходом по Левенштейну.

    Возвращает список (op, ref, hyp): op in {"ok","sub","ins","del"}.
    При ничьих приоритет: sub > del > ins (детерминировано).
    """
    m, n = len(ref_words), len(hyp_words)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if ref_words[i - 1] == hyp_words[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = 1 + min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1])
    ops: list[tuple[str, str | None, str | None]] = []
    i, j = m, n
    while i > 0 or j > 0:
        if i > 0 and j > 0 and ref_words[i - 1] == hyp_words[j - 1]:
            ops.append(("ok", ref_words[i - 1], hyp_words[j - 1]))
            i -= 1
            j -= 1
        elif i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + 1:
            ops.append(("sub", ref_words[i - 1], hyp_words[j - 1]))
            i -= 1
            j -= 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            ops.append(("del", ref_words[i - 1], None))
            i -= 1
        else:
            ops.append(("ins", None, hyp_words[j - 1]))
            j -= 1
    ops.reverse()
    return ops


def levenshtein_distance(seq1, seq2) -> int:
    m, n = len(seq1), len(seq2)
    dp = [[0] * (n + 1) for _ in range(m + 1)]

    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if seq1[i - 1] == seq2[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = 1 + min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1])

    return dp[m][n]
