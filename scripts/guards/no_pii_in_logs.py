#!/usr/bin/env python3
"""Guard (эвристика): в вызовах логирования не должно быть сырых текстов расшифровок и ПД.

Ловит типичные ошибки вида logger.info(f"... {transcript} ...") и print(text).
Это не замена ревью и тестам на маскирование, а дешёвая первая линия.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "tests",
    "scripts",
    "docs",
    ".opencode",
}
LOG_CALL = re.compile(
    r"\b(log|logger|logging)\.(debug|info|warning|error|exception|critical)\(|\bprint\("
)
SENSITIVE = re.compile(
    r"\b(transcript|transcription|text|utterance|phone|email|e_mail|card|passport|segments|words)\b",
    re.I,
)
SAFE_HINT = re.compile(r"(len\(|_len|_count|mask|redact|hash|\.id\b|_id\b|duration|size)", re.I)


def main() -> int:
    bad = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT)
        if set(rel.parts) & SKIP_DIRS:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if LOG_CALL.search(line) and SENSITIVE.search(line) and not SAFE_HINT.search(line):
                bad.append(f"{rel}:{n}: {line.strip()[:120]}")
    if bad:
        print("ПОДОЗРЕНИЕ: возможна запись ПД или текста расшифровки в лог:")
        print("\n".join(bad))
        return 1
    print("guard no_pii_in_logs: ок")
    return 0


if __name__ == "__main__":
    sys.exit(main())
