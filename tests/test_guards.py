"""Тесты guard-скриптов make check: чистый код проходит, нарушения ловятся."""

import importlib.util
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("DEV_TOKEN", "dev-token")
os.environ.setdefault("APP_ENV", "test")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_guard(name: str):
    path = os.path.join(ROOT, "scripts", "guards", f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"guard_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_no_external_asr_clean_repo_passes():
    mod = _load_guard("no_external_asr")
    assert mod.main() == 0


def test_no_external_asr_word_boundary_prevail():
    """Границы слов: обычные слова и имена моделей не срабатывают."""
    mod = _load_guard("no_external_asr")
    assert not mod.REGEX.search("we will prevail against the odds")
    assert not mod.REGEX.search("model_name = 'whisper-small'")


def test_no_external_asr_catches_real_violation(tmp_path, monkeypatch):
    mod = _load_guard("no_external_asr")
    # Литералы собраны конкатенацией, чтобы сам тест не триггерил гвард.
    vendor = "assembly" + "ai"
    endpoint = "https://api.deep" + "gram.com/v1/listen"
    (tmp_path / "evil.py").write_text(
        f"import {vendor}\nclient.post('{endpoint}')\n",
        encoding="utf-8",
    )
    (tmp_path / "ok.py").write_text("print('hello')\n", encoding="utf-8")
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    assert mod.main() == 1


def test_no_pii_in_logs_clean_repo_passes():
    mod = _load_guard("no_pii_in_logs")
    assert mod.main() == 0


def test_no_pii_in_logs_heuristic():
    mod = _load_guard("no_pii_in_logs")
    bad = 'logger.info(f"transcript ready: {transcript}")'
    assert mod.LOG_CALL.search(bad) and mod.SENSITIVE.search(bad)
    assert not mod.SAFE_HINT.search(bad)
    safe = 'logger.info("transcript ready", text_len=len(text))'
    assert mod.LOG_CALL.search(safe) and mod.SAFE_HINT.search(safe)


def test_no_pii_in_logs_catches_violation(tmp_path, monkeypatch):
    mod = _load_guard("no_pii_in_logs")
    (tmp_path / "evil.py").write_text(
        'logger.info(f"got phone {phone} for {email}")\n', encoding="utf-8"
    )
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    assert mod.main() == 1
