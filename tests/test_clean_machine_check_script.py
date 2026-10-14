"""Проверки scripts/clean_machine_check.sh по требованиям задачи E6.

1. Клон и модели — в каталог на диске (не /tmp/tmpfs), путь через CLEAN_CHECK_BASE.
2. Префлайт: место >= 15 ГБ и свободный порт, падение с понятной причиной.
3. При падении: последние 40 строк лога + имя упавшего сервиса.
4. После прогона: удаление временного клона и томов только своего project name.

Живые тесты используют стабы git/df/curl в PATH и падают на префлайте
за секунды — рабочий стек, docker и сеть не затрагиваются, sudo не нужен.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "clean_machine_check.sh"


@pytest.fixture(scope="module")
def text():
    return SCRIPT.read_text(encoding="utf-8")


def test_script_syntax_ok():
    r = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, f"bash -n: {r.stderr}"


def test_clone_base_on_disk_not_tmp(text):
    """Клон и временные файлы — под CLEAN_CHECK_BASE на диске, не в /tmp."""
    assert "CLEAN_CHECK_BASE" in text
    assert 'CLONE_DIR="/tmp' not in text
    assert "/tmp/opencode" not in text
    assert "CLONE_DIR=" in text and "BASE_DIR" in text
    # Дефолт — кэш на диске, а не tmpfs.
    assert ".cache" in text and "apc-clean" in text


def test_disk_space_preflight(text):
    """Проверка места (15 ГБ) с понятной причиной падения."""
    assert "MIN_DISK_GB=15" in text
    assert "df " in text and "CLEAN_CHECK_BASE" in text
    assert "нужно не менее" in text and "15" in text


def test_port_preflight_with_reason(text):
    """Проверка свободного порта с понятной причиной (не просто 'up неуспешен')."""
    assert "CLEAN_CHECK_HOST_PORT" in text
    assert "уже занят" in text
    assert "compose up неуспешен дважды (см. лог сборки)" not in text


def test_failure_diagnostics_tail40_and_service_name(text):
    """При падении: последние 40 строк лога и имя упавшего сервиса."""
    assert 'tail -n 40 "${UPLOG}"' in text
    assert "logs --no-color --tail=40" in text
    assert "упавшие/нездоровые сервисы" in text
    assert "dump_failure_diagnostics" in text


def test_cleanup_scoped_down_v_and_rm_clone(text):
    """down -v только со своим -p project и удаление временного клона."""
    assert "down -v" in text
    assert "-p ${PROJECT}" in text
    assert 'rm -rf "${CLONE_DIR}"' in text
    # Голого `down` без -v (оставлял чужие/свои тома) быть не должно.
    import re

    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            continue
        if re.search(r"(?<![\w-])down(?![\w-])", s):
            assert "-v" in s, f"down без -v: {s}"
    assert "клон оставлен" not in text


def _write_stub(bin_dir: Path, name: str, body: str):
    p = bin_dir / name
    p.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _run_with_stubs(tmp_path: Path, df_avail_kb: int, curl_ok: bool):
    """Запуск скрипта со стабами git/df/curl; падает на префлайте за секунды."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stub(
        bin_dir,
        "git",
        'if [ "$1" = "rev-parse" ]; then echo deadbee; fi\nexit 0',
    )
    _write_stub(bin_dir, "df", f'echo "Avail"\necho "{df_avail_kb}"')
    _write_stub(bin_dir, "curl", "exit 0" if curl_ok else "exit 1")
    env = dict(
        os.environ,
        CLEAN_CHECK_BASE=str(tmp_path / "base"),
        PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
    )
    return subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def test_live_low_disk_fails_with_clear_reason(tmp_path):
    r = _run_with_stubs(tmp_path, df_avail_kb=5 * 1024 * 1024, curl_ok=False)
    assert r.returncode == 1
    out = r.stdout + r.stderr
    assert "нужно не менее 15 ГБ" in out
    assert "CLEAN_CHECK_BASE" in out


def test_live_busy_port_fails_with_clear_reason(tmp_path):
    r = _run_with_stubs(tmp_path, df_avail_kb=30 * 1024 * 1024, curl_ok=True)
    assert r.returncode == 1
    out = r.stdout + r.stderr
    assert "уже занят" in out
    assert "CLEAN_CHECK_HOST_PORT" in out
