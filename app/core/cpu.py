"""Проверка CPU-флагов (AVX2) — см. known-issues.md п.5-6.

CTranslate2 int8-ядра без AVX2 деградируют на порядок: voice-SLA и RTF
невыполнимы. Читаем флаги из /proc/cpuinfo (Linux); вне Linux — False.
"""

from pathlib import Path

_CPUINFO = Path("/proc/cpuinfo")


def cpu_flags(path: str | Path = _CPUINFO) -> set[str]:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return set()
    flags: set[str] = set()
    for line in text.splitlines():
        if line.startswith("flags") and ":" in line:
            flags.update(line.split(":", 1)[1].split())
    return flags


def has_avx2(path: str | Path = _CPUINFO) -> bool:
    """True, если CPU сообщает флаг avx2 (точное совпадение токена)."""
    return "avx2" in cpu_flags(path)
