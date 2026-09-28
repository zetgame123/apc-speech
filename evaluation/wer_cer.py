import re
from typing import Tuple

from app.asr.normalizer import RussianTextNormalizer

normalizer = RussianTextNormalizer()


def normalize_eval_text(text: str) -> str:
    """Applies Russian ITN, lowercases, strips punctuation, and collapses whitespaces."""
    if not text:
        return ""
    # 1. Apply ITN normalizer (converts "три на шесть" to "3×6", numbers, etc.)
    t = normalizer.normalize(text)
    # 2. Lowercase and remove punctuation except standard word characters
    t = re.sub(r"[^\w\s×]", " ", t.lower())
    # 3. Collapse multiple spaces
    t = re.sub(r"\s+", " ", t).strip()
    return t


def calculate_wer_cer(reference: str, hypothesis: str) -> Tuple[float, float]:
    """Computes Word Error Rate (WER) and Character Error Rate (CER)."""
    ref_norm = normalize_eval_text(reference)
    hyp_norm = normalize_eval_text(hypothesis)

    ref_words = ref_norm.split()
    hyp_words = hyp_norm.split()

    # Levenshtein distance on words
    d_words = levenshtein_distance(ref_words, hyp_words)
    wer = d_words / len(ref_words) if ref_words else 0.0

    # Levenshtein distance on characters
    d_chars = levenshtein_distance(list(ref_norm), list(hyp_norm))
    cer = d_chars / len(ref_norm) if ref_norm else 0.0

    return round(wer, 4), round(cer, 4)


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
