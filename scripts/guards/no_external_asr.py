#!/usr/bin/env python3
"""Guard: в коде не должно быть клиентов внешних сервисов распознавания речи.

Проверяет только КОД и КОНФИГИ проекта (не веса моделей, не данные, не зависимости в .venv).
Паттерны привязаны к границам слов, чтобы не ловить обычные слова (например, "prevail").
Код возврата 1, если найдено нарушение.
"""

import re
import sys
from pathlib import Path

# Вывод в UTF-8 независимо от кодировки консоли (на Windows cp1251 иначе падает UnicodeEncodeError).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

ROOT = Path(__file__).resolve().parents[2]
SCAN_SUFFIXES = {
    ".py",
    ".toml",
    ".txt",
    ".cfg",
    ".ini",
    ".yaml",
    ".yml",
    ".env",
    ".example",
    ".js",
    ".ts",
}
SCAN_NAMES = {"Dockerfile", "docker-compose.yml", "docker-compose.yaml"}
SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "models",
    "data",
    "reports",
    "build",
    "dist",
    ".opencode",
    "docs",
}
# Сами guard-скрипты содержат список паттернов, их не сканируем (остальной scripts/ сканируем).
SKIP_PREFIXES = ("scripts/guards/",)
SKIP_FILES = {"uv.lock", "poetry.lock", "package-lock.json"}
ALLOWLIST: set[str] = set()

PATTERNS = [
    r"speech\.googleapis\.com",
    r"google-cloud-speech",
    r"from\s+google\.cloud\s+import\s+speech",
    r"transcribe\.[a-z0-9-]+\.amazonaws\.com",
    r"\bamazon_transcribe\b",
    r"cognitiveservices\.azure\.com",
    r"azure-cognitiveservices-speech",
    r"api\.openai\.com/v1/audio",
    r"\.audio\.transcriptions\b",
    r"api\.assemblyai\.com",
    r"\bassemblyai\b",
    r"api\.deepgram\.com",
    r"\bdeepgram\b",
    r"api\.speechmatics\.com",
    r"\bspeechmatics\b",
    r"api\.rev\.ai",
    r"\brev[-_.]?ai\b",
    r"stt\.api\.cloud\.yandex\.net",
    r"\bspeechkit\b",
    r"smartspeech\.sber",
    r"\bsalute[-_]?speech\b",
    r"api\.elevenlabs\.io/v1/speech-to-text",
    r"\bvoicekit\b",
    r"api\.groq\.com/openai/v1/audio",
]
REGEX = re.compile("|".join(PATTERNS), re.IGNORECASE)


def should_scan(path: Path) -> bool:
    if path.name in SKIP_FILES:
        return False
    return path.suffix in SCAN_SUFFIXES or path.name in SCAN_NAMES


def main() -> int:
    bad = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or not should_scan(path):
            continue
        rel = path.relative_to(ROOT)
        # Пропускаем служебные каталоги на любом уровне, но app/, evaluation/, tests/ сканируем.
        rel_posix = rel.as_posix()
        if (
            set(rel.parts[:-1]) & SKIP_DIRS
            or rel_posix in ALLOWLIST
            or rel_posix.startswith(SKIP_PREFIXES)
        ):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if REGEX.search(line):
                bad.append(f"{rel}:{n}: {line.strip()[:120]}")
    if bad:
        print("НАРУШЕНИЕ: найдены признаки внешнего ASR (аудио не должно покидать сервер):")
        print("\n".join(bad))
        return 1
    print("guard no_external_asr: ок")
    return 0


if __name__ == "__main__":
    sys.exit(main())
