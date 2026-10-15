"""Пункт 1 доработки W1: безопасность дашборда.

- Страницы дашборда отдают заголовки CSP/X-Content-Type-Options/Referrer-Policy/Cache-Control.
- Статика (вендор, css/js) раздаётся самим api, внешних ресурсов из интернета нет.
- Нет инлайн-скриптов/стилей/обработчиков (условие работы script-src/style-src 'self').
- В разметке нет дефолтного токена; общий escape-хелпер экранирует XSS-векторы.
"""

import json
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
    "img-src 'self' data:; media-src 'self'; connect-src 'self'; "
    "frame-ancestors 'none'"
)


def _parse_csp(csp: str) -> dict:
    directives = {}
    for part in csp.split(";"):
        part = part.strip()
        if not part:
            continue
        name, _, values = part.partition(" ")
        directives[name] = values.split()
    return directives


def test_dashboard_csp_keeps_scripts_strict():
    """Пп.1–2 ручной проверки: скрипты/стили строго 'self' (WaveSurfer удалён,
    плеер — собственный WebAudio без инлайн-стилей и fetch(blob:)); data:
    разрешён только в img-src для SVG-стрелок Bootstrap (ADR-025)."""
    csp = _parse_csp(EXPECTED_CSP)
    assert csp["script-src"] == ["'self'"], csp["script-src"]
    assert csp["style-src"] == ["'self'"], csp["style-src"]
    for name, values in csp.items():
        assert "'unsafe-eval'" not in values, f"{name}: {values}"
        assert "'unsafe-inline'" not in values, f"{name}: {values}"
        if name != "img-src":
            assert "data:" not in values, f"{name}: {values}"
            assert "blob:" not in values, f"{name}: {values}"
    assert "data:" in csp["img-src"], "data:-SVG стрелок select заблокированы"


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
async def test_dashboard_static_missing_file_is_404():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/static/js/no-such-file.js")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_dashboard_pages_have_expected_markers_and_nav():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        dash = await client.get("/dashboard")
        assert "APC-SPEECH" in dash.text and "waveform" in dash.text
        assert 'href="/voice"' in dash.text
        assert "wavesurfer" not in dash.text.lower(), "WaveSurfer удалён (ADR-025)"
        assert "/static/js/player.js" in dash.text
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
            ("/static/js/player.js", "decodeAudioData"),
            ("/static/js/dashboard.js", "refreshCallsList"),
            ("/static/js/voice.js", "fetchVoiceHistory"),
            ("/static/css/dashboard.css", ".auth-gate-overlay"),
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


def _run_dashboard_page_load_check(node, files, need):
    """Грузит общие модули + одну страницу в node с DOM-заглушками.

    Ловит ReferenceError/SyntaxError уровня загрузки (регрессия W1.6:
    getHeaders/getNamespace были только на странице звонков).
    """
    harness = """
const fs = require('fs');
const dir = process.argv[1];
const files = JSON.parse(process.argv[2]);
const need = JSON.parse(process.argv[3]);
const srcParts = [];
function makeEl() {
  return new Proxy(function () {}, {
    get(t, p) {
      if (p === 'style') return {};
      if (p === 'dataset') return {};
      if (p === 'classList') return { add() {}, remove() {}, toggle() {}, contains() { return false; } };
      if (p === Symbol.toPrimitive) return () => '';
      if (p === 'length') return 0;
      if (p === 'then') return undefined;
      return makeEl();
    },
    set() { return true; },
    apply() { return makeEl(); },
  });
}
global.window = { fetch: async () => { throw new Error('stub fetch'); } };
global.fetch = global.window.fetch;
global.sessionStorage = { getItem: () => null, setItem: () => {} };
global.document = new Proxy({}, {
  get(t, p) {
    if (p === 'readyState') return 'complete';
    if (p === 'querySelectorAll') return () => [];
    return makeEl();
  },
  set() { return true; },
});
global.navigator = {};
for (const f of files) srcParts.push(fs.readFileSync(dir + '/' + f, 'utf-8'));
try {
  eval.call(global, srcParts.join('\\n;\\n'));
} catch (e) {
  console.error(`FAIL load: ${e.constructor.name}: ${e.message}`);
  process.exit(1);
}
for (const fn of ['getHeaders', 'getNamespace', 'escapeHtml', 'formatTime', ...need]) {
  let ok = false;
  try { const v = eval(fn); ok = typeof v === 'function' || (v !== null && typeof v === 'object'); } catch (e) { ok = false; }
  if (!ok) { console.error(`FAIL: no function ${fn}`); process.exit(1); }
}
console.log('page load OK');
"""
    proc = subprocess.run(
        [node, "-e", harness, str(STATIC_DIR / "js"), json.dumps(files), json.dumps(need)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (proc.stderr or proc.stdout)[-2000:]
    assert "page load OK" in proc.stdout
    assert "FAIL" not in (proc.stderr or "")


def test_dashboard_pages_load_without_js_reference_errors():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node отсутствует в окружении")
    _run_dashboard_page_load_check(
        node,
        ["escape.js", "api.js", "format.js", "player.js", "dashboard.js"],
        ["refreshCallsList", "openArchivedCall", "renderAnalysis", "Player"],
    )
    _run_dashboard_page_load_check(
        node,
        ["escape.js", "api.js", "format.js", "voice.js"],
        ["fetchSummary", "exportSummaryCsv", "fetchVoiceHistory", "runTopicClustering"],
    )


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


def test_dashboard_analysis_follows_real_schema():
    """Пункт 5: разбор по schemas/analysis_schema_v1.json, без неверных ключей."""
    js = (STATIC_DIR / "js" / "dashboard.js").read_text(encoding="utf-8")
    for marker in (
        "target_period",
        "product_category",
        "client_overall",
        "callResultBadge",
        "agreementsBox",
        "metricSilence",
        "manager_speech_share",
        "client_speech_share",
        "silence_duration_sec",
        "manager_wpm",
        "client_wpm",
        "schema_version",
        "prompt_version",
    ):
        assert marker in js, f"нет маркера схемы: {marker}"
    for wrong in (
        "manager_speech_ratio",
        "client_speech_ratio",
        "words_per_minute",
        "sent.overall",
    ):
        assert wrong not in js, f"неверный ключ схемы: {wrong}"
    html = (STATIC_DIR / "dashboard.html").read_text(encoding="utf-8")
    for el in ("callResultBadge", "schemaVersions", "agreementsBox", "metricSilence"):
        assert f'id="{el}"' in html, f"нет элемента {el}"


def test_dashboard_call_card_player_and_highlight():
    """Пункт 4 + п.2 ручной проверки: собственный WebAudio-плеер
    (decodeAudioData, честный статус декодирования), подсветка реплики."""
    js = (STATIC_DIR / "js" / "dashboard.js").read_text(encoding="utf-8")
    assert "wavesurfer" not in js.lower() and "WaveSurfer" not in js, "остался WaveSurfer"
    player = (STATIC_DIR / "js" / "player.js").read_text(encoding="utf-8")
    for marker in (
        "decodeAudioData",
        "onTimeUpdate",
        "onReady",
        "onError",
        "wave-canvas",
        "getCurrentTime",
        "setTime",
    ):
        assert marker in player, f"player.js: нет маркера: {marker}"
    for marker in (
        "MAX_BROWSER_AUDIO_BYTES",
        "replica-active",
        "activeBubble",
        "renderCallCardHeader",
        "callCardHeader",
        "слишком большое для браузерного плеера",
        # П.2 ручной проверки: честный статус декодирования, без вранья.
        "Player.loadBlob",
        "Player.onReady",
        "Player.onError",
        "Player.setTime",
        "Player.toggle",
        "Player.play",
        "Загрузка аудио в плеер",
        "Аудио загружено в плеер",
        "Не удалось подготовить аудио к воспроизведению",
    ):
        assert marker in js, f"нет маркера: {marker}"
    html = (STATIC_DIR / "dashboard.html").read_text(encoding="utf-8")
    for el in ("callCardHeader", "callCardTitle", "callCardMeta", "callCardFlags"):
        assert f'id="{el}"' in html, f"нет элемента {el}"


def test_dashboard_no_blocking_dialogs_and_guarded_formats():
    """Пункт 8: без alert/confirm, лимит записи 60 с, защита toFixed/Math.max."""
    for name in ("dashboard.js", "voice.js", "api.js", "format.js", "player.js"):
        text = (STATIC_DIR / "js" / name).read_text(encoding="utf-8")
        assert "alert(" not in text, f"{name}: остался alert"
        assert "confirm(" not in text, f"{name}: остался confirm"
    voice = (STATIC_DIR / "js" / "voice.js").read_text(encoding="utf-8")
    assert "VOICE_RECORD_LIMIT_SEC = 60" in voice
    assert "0.25x" not in voice
    dash = (STATIC_DIR / "js" / "dashboard.js").read_text(encoding="utf-8")
    assert "fmtSec(" in dash and "pollNs" in dash and "clearHistoryArmed" in voice
    assert "Math.max(..." not in dash and "Math.max(..." not in voice
    fmt = (STATIC_DIR / "js" / "format.js").read_text(encoding="utf-8")
    assert "function fmtSec" in fmt and "function toIsoOrNull" in fmt
    for name in ("dashboard.html", "voice.html"):
        html = (STATIC_DIR / name).read_text(encoding="utf-8")
        assert "<th>" not in html, f"{name}: th без scope"
        assert 'scope="col"' in html, f"{name}: нет scope=col"


def test_dashboard_clusters_have_two_sources():
    """Пункт 7: кластеры из истории запросов и из текстов звонков (существующие ручки)."""
    html = (STATIC_DIR / "voice.html").read_text(encoding="utf-8")
    assert 'id="clusterSourceHistory"' in html
    assert 'id="clusterSourceCalls"' in html
    js = (STATIC_DIR / "js" / "voice.js").read_text(encoding="utf-8")
    for marker in (
        "collectCallTexts",
        "clusterSourceCalls",
        "/v1/calls/${c.call_id}/transcript",
        "renderClusters",
        "representative_queries",
    ):
        assert marker in js, f"нет маркера: {marker}"


def test_dashboard_summary_section():
    """Пункт 6: сводка — фильтры, распределения, тренды, экспорт CSV (существующие ручки)."""
    html = (STATIC_DIR / "voice.html").read_text(encoding="utf-8")
    for el in (
        "summaryFilterForm",
        "sumFrom",
        "sumTo",
        "sumManager",
        "sumType",
        "summaryFilterError",
        "summaryBody",
        "summaryTopics",
        "summaryEntities",
        "summaryChecklist",
        "summaryTrends",
        "btnRefreshSummary",
        "btnExportSummaryCsv",
    ):
        assert f'id="{el}"' in html, f"нет элемента {el}"
    js = (STATIC_DIR / "js" / "voice.js").read_text(encoding="utf-8")
    for marker in (
        "/v1/analytics/summary",
        "/v1/analytics/export_csv",
        "fetchSummary",
        "exportSummaryCsv",
        "weekly_trends",
        "inquiry_distribution",
        "top_entities",
        "checklist_averages",
        "summaryBarRow",
    ):
        assert marker in js, f"нет маркера: {marker}"
    assert "cdn." not in js.lower() and "unpkg" not in js.lower()


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
