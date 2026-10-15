"""Пункт 1 доработки W1: безопасность дашборда.

- Страницы дашборда отдают заголовки CSP/X-Content-Type-Options/Referrer-Policy/Cache-Control.
- Статика (вендор, css/js) раздаётся самим api, внешних ресурсов из интернета нет.
- Нет инлайн-скриптов/стилей/обработчиков (условие работы script-src/style-src 'self').
- В разметке нет дефолтного токена; общий escape-хелпер экранирует XSS-векторы.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

STATIC_DIR = Path("app/static")
DASHBOARD_PAGES = ["/", "/dashboard", "/ui", "/dashboard-modern", "/ui-modern", "/voice"]

EXPECTED_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'"
)


@pytest.mark.asyncio
async def test_dashboard_pages_have_security_headers():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for url in DASHBOARD_PAGES:
            resp = await client.get(url)
            assert resp.status_code == 200, url
            assert resp.headers["content-security-policy"] == EXPECTED_CSP, url
            assert resp.headers["x-content-type-options"] == "nosniff", url
            assert resp.headers["referrer-policy"] == "no-referrer", url
            assert resp.headers["cache-control"] == "no-store", url


@pytest.mark.asyncio
async def test_dashboard_classic_redirects_to_dashboard():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for url in ("/dashboard-classic", "/ui-classic"):
            resp = await client.get(url, follow_redirects=False)
            assert resp.status_code == 303, url
            assert resp.headers["location"] == "/dashboard", url
            assert "content-security-policy" in resp.headers, url


@pytest.mark.asyncio
async def test_dashboard_pages_reference_only_existing_static():
    """Все /static/-ссылки страниц указывают на существующие файлы (без 404)."""
    refs = set()
    for name in ("dashboard.html", "voice.html"):
        text = (STATIC_DIR / name).read_text(encoding="utf-8")
        refs.update(re.findall(r'(?:src|href)="(/static/[^"]+)"', text))
    assert refs, "нет static-ссылок"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for url in sorted(refs):
            resp = await client.get(url)
            assert resp.status_code == 200, url


@pytest.mark.asyncio
async def test_dashboard_pages_have_expected_markers_and_nav():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        dash = await client.get("/dashboard")
        assert "APC-SPEECH" in dash.text and "audioPlayer" in dash.text
        assert 'href="/voice"' in dash.text
        voice = await client.get("/voice")
        assert "APC-SPEECH" in voice.text and "voiceTextResult" in voice.text
        assert 'href="/dashboard"' in voice.text


@pytest.mark.asyncio
async def test_dashboard_static_served_by_api():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for url, marker in (
            ("/static/js/escape.js", "function escapeHtml"),
            ("/static/js/api.js", "getToken"),
            ("/static/js/format.js", "function formatTime"),
            ("/static/js/dashboard.js", "refreshCallsList"),
            ("/static/js/voice.js", "fetchVoiceHistory"),
            ("/static/css/dashboard.css", ".auth-gate-overlay"),
            ("/static/vendor/wavesurfer-7.12.12/wavesurfer.min.js", "WaveSurfer"),
            ("/static/vendor/bootstrap-5.3.2/bootstrap.min.css", "Bootstrap"),
            (
                "/static/vendor/bootstrap-icons-1.11.3/bootstrap-icons.min.css",
                "bootstrap-icons",
            ),
        ):
            resp = await client.get(url)
            assert resp.status_code == 200, url
            assert marker in resp.text, url


def _static_text_files():
    return [
        p for p in STATIC_DIR.rglob("*") if p.is_file() and p.suffix in {".html", ".js", ".css"}
    ]


def test_dashboard_static_has_no_external_resources():
    """Никаких внешних ресурсов из интернета: CDN, шрифты, аналитика."""
    patterns = [
        r'src="https?://',
        r"href=\"https?://",
        r"url\(https?://",
        r"@import\s+['\"]https?://",
        r"cdn\.jsdelivr\.net",
        r"unpkg\.com",
        r"googleapis\.com",
        r"gstatic\.com",
        r"googletagmanager\.com",
        r"googletagservices\.com",
    ]
    bad = []
    for path in _static_text_files():
        text = path.read_text(encoding="utf-8")
        for pat in patterns:
            if re.search(pat, text):
                bad.append(f"{path}:{pat}")
    assert not bad, f"внешние ресурсы в статике: {bad}"


def test_dashboard_html_has_no_inline_code_or_default_token():
    """Условие CSP script-src/style-src 'self' + отсутствие дефолтного токена."""
    for name in ("dashboard.html", "voice.html"):
        text = (STATIC_DIR / name).read_text(encoding="utf-8")
        assert "<style" not in text, f"{name}: инлайн-<style>"
        assert ' style="' not in text, f"{name}: инлайн style="
        assert "<script" not in text or "<script src=" in text, f"{name}: скрипт?"
        inline_scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", text)
        assert not inline_scripts, f"{name}: инлайн-<script>: {inline_scripts}"
        handlers = re.findall(r"\son[a-z]+\s*=", text)
        assert not handlers, f"{name}: инлайн-обработчики: {handlers}"
        assert "dev-token" not in text, f"{name}: дефолтный токен в разметке"
        assert "onclick" not in text, f"{name}: inline onclick"


def test_dashboard_js_uses_escaped_render_for_voice_text():
    """ASR-текст экранируется до подсветки терминов (бывший XSS, audit B1)."""
    voice = (STATIC_DIR / "js" / "voice.js").read_text(encoding="utf-8")
    assert "escapeHtml(cleanText)" in voice
    for name in ("dashboard.js", "voice.js", "api.js", "format.js"):
        text = (STATIC_DIR / "js" / name).read_text(encoding="utf-8")
        assert "function escapeHtml" not in text, f"{name}: дублирующий хелпер"


def test_dashboard_calls_list_has_filters_and_cursor_pagination():
    """Пункт 3: фильтры, курсорная пагинация, состояния — на существующих параметрах API."""
    html = (STATIC_DIR / "dashboard.html").read_text(encoding="utf-8")
    for el in (
        "fltQ",
        "fltManager",
        "fltType",
        "fltFrom",
        "fltTo",
        "fltLimit",
        "btnMoreCalls",
        "btnSortDir",
        "btnResetFilters",
        "callsFilterError",
        "callsCount",
    ):
        assert f'id="{el}"' in html, f"нет элемента {el}"
    assert 'value="pricing"' in html and 'value="availability"' in html
    js = (STATIC_DIR / "js" / "dashboard.js").read_text(encoding="utf-8")
    for marker in (
        "next_cursor",
        "URLSearchParams",
        "params.set('q'",
        "params.set('manager_id'",
        "params.set('inquiry_type'",
        "params.set('date_from'",
        "params.set('date_to'",
        "params.set('cursor'",
        "Загрузить ещё",
        "Нет звонков по заданным фильтрам",
        "Загрузка звонков...",
    ):
        assert marker in js, f"нет маркера: {marker}"
    assert "&limit=15" not in js, "остался захардкоженный limit=15"


def test_escape_html_helper_vectors():
    """Общий escape-хелпер: XSS-векторы обезвреживаются, обычный текст цел."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node отсутствует в окружении")
    helper = (STATIC_DIR / "js" / "escape.js").resolve()
    script = (
        f"const h = require({str(helper)!r});"
        "const cases = ["
        "  ['<img src=x onerror=alert(1)>', false],"
        "  ['<script>alert(1)</scr' + 'ipt>', false],"
        "  ['<svg onload=alert(1)>', false],"
        "  ['\"\\'&<>', false],"
        "  ['3×6 в Балашихе, щиты', true],"
        "  ['Привет, Менеджер!', true],"
        "];"
        "for (const [input, keep] of cases) {"
        "  const out = h.escapeHtml(input);"
        "  if (/[<>]/.test(out)) { console.error('FAIL raw brackets: ' + input); process.exit(1); }"
        "  if (keep && out !== input) { console.error('FAIL altered: ' + input); process.exit(1); }"
        "}"
        "if (h.escapeHtml(null) !== '' || h.escapeHtml(undefined) !== '' || h.escapeHtml(123) !== '123')"
        "  { console.error('FAIL null-safety'); process.exit(1); }"
        "console.log('escape vectors OK');"
    )
    proc = subprocess.run(
        [node, "-e", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "escape vectors OK" in proc.stdout
