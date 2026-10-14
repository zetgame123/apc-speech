"""Сверка README с кодом: env-переменные, ссылки, quick-start (D4).

1. Каждая переменная из `.env.example` (активная `VAR=` и закомментированная
   `# VAR=`) описана в env-таблице README либо в `docs/*`, на который README
   ссылается.
2. Каждая переменная из env-таблицы README есть в `.env.example`, кроме
   операционных исключений (см. OPERATIONAL_VARS: проверяются по источнику).
3. Все ссылки README на файлы ведут на существующие файлы; якоря `#...`
   соответствуют заголовкам; ручки quick-start есть в `openapi.yaml`.
   Долгая сборка не гоняется здесь — она покрыта `scripts/clean_machine_check.sh`.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
ENV_EXAMPLE = ROOT / ".env.example"

# Переменные, упомянутые в README, но не являющиеся сервисным env:
# проверяются по указанному источнику, а не по .env.example.
OPERATIONAL_VARS = {
    # Не сервисный env: compose выставляет из ASR_THREADS / пинит 1 в воркерах.
    "OMP_NUM_THREADS": "docker-compose.yml",
    # Переменные скрипта чистой машины, не сервиса.
    "CLEAN_CHECK_BASE": "scripts/clean_machine_check.sh",
    "CLEAN_CHECK_HOST_PORT": "scripts/clean_machine_check.sh",
}


def _env_example_vars() -> tuple[set[str], set[str]]:
    active, commented = set(), set()
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        m = re.match(r"([A-Z][A-Z0-9_]*)\s*=", s)
        if m:
            active.add(m.group(1))
            continue
        m = re.match(r"#\s*([A-Z][A-Z0-9_]*)\s*=", s)
        if m:
            commented.add(m.group(1))
    return active, commented


def _readme_env_table_vars() -> set[str]:
    """Переменные из первой колонки env-таблицы README (с раскрытием `_СУФФИКС`)."""
    text = README.read_text(encoding="utf-8")
    start = text.index("Переменные окружения")
    section = text[start:]
    end = section.index("\n---", start and 0)
    table = section[:end]
    out: set[str] = set()
    for line in table.splitlines():
        if not line.strip().startswith("|"):
            continue
        first_cell = line.split("|")[1]
        prefix = ""
        for tok in re.findall(r"`([^`]+)`", first_cell):
            if re.fullmatch(r"[A-Z][A-Z0-9_]*", tok):
                out.add(tok)
                prefix = tok.rsplit("_", 1)[0] if "_" in tok else tok
            elif re.fullmatch(r"_[A-Z][A-Z0-9_]*", tok) and prefix:
                out.add(prefix + tok)
    return out


def _linked_docs_text() -> str:
    readme = README.read_text(encoding="utf-8")
    parts = [readme]
    for link in sorted(set(re.findall(r"\(docs/([^)]+)\)", readme))):
        p = ROOT / "docs" / link
        if p.is_file():
            parts.append(p.read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_settings_aliases_in_env_example():
    """Каждый alias app/core/config.py есть в .env.example (иначе скрытый рубильник)."""
    config = (ROOT / "app" / "core" / "config.py").read_text(encoding="utf-8")
    aliases = set(re.findall(r'alias="([A-Z][A-Z0-9_]*)"', config))
    assert aliases, "алиасы настроек не распознаны"
    active, commented = _env_example_vars()
    missing = sorted(a for a in aliases if a not in active and a not in commented)
    assert not missing, f"настройки кода без отражения в .env.example: {missing}"


def test_env_example_vars_documented():
    active, commented = _env_example_vars()
    assert active, ".env.example не содержит активных VAR="
    documented = _readme_env_table_vars()
    corpus = _linked_docs_text()
    missing = [v for v in sorted(active | commented) if v not in documented and v not in corpus]
    assert not missing, f"переменные из .env.example без описания: {missing}"


def test_readme_table_vars_exist_in_env():
    active, commented = _env_example_vars()
    known = active | commented
    table_vars = _readme_env_table_vars()
    assert table_vars, "env-таблица README не распознана"
    for var, source in OPERATIONAL_VARS.items():
        if var in table_vars or var in README.read_text(encoding="utf-8"):
            src = (ROOT / source).read_text(encoding="utf-8")
            assert var in src, f"{var} заявлен как операционный, но нет в {source}"
    unknown = [v for v in sorted(table_vars) if v not in known and v not in OPERATIONAL_VARS]
    assert not unknown, f"в README описаны переменные, которых нет в .env.example: {unknown}"


def test_readme_file_links_exist():
    readme = README.read_text(encoding="utf-8")
    links = sorted(set(re.findall(r"\((docs/[^)]+|\.env\.example|openapi\.yaml)\)", readme)))
    assert links, "в README не найдено ссылок на файлы"
    missing = [link for link in links if not (ROOT / link).is_file()]
    assert not missing, f"битые ссылки README: {missing}"
    assert (ROOT / "scripts" / "clean_machine_check.sh").is_file()


def test_readme_anchors_match_headings():
    readme = README.read_text(encoding="utf-8")
    anchors = set(re.findall(r"\(#([^)]+)\)", readme))
    assert anchors, "в README нет якорей"
    slugs = set()
    for line in readme.splitlines():
        m = re.match(r"#{1,6}\s+(.+)", line.strip())
        if m:
            slugs.add(m.group(1).strip().lower().replace(" ", "-"))
    broken = [a for a in sorted(anchors) if a not in slugs]
    assert not broken, f"якоря без заголовков: {broken}"


def test_quickstart_endpoints_in_openapi():
    spec = (ROOT / "openapi.yaml").read_text(encoding="utf-8")
    for path in (
        "/v1/jobs/upload",
        "/v1/jobs/{job_id}",
        "/v1/calls/{call_id}/transcript",
        "/v1/voice/search",
        "/v1/voice/sample-audio",
        "/health",
        "/ready",
        "/metrics",
    ):
        assert path in spec, f"ручка quick-start {path} отсутствует в openapi.yaml"
