import os

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter(tags=["dashboard"])

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
DASHBOARD_PATH = os.path.join(STATIC_DIR, "dashboard.html")
VOICE_PATH = os.path.join(STATIC_DIR, "voice.html")

# Заголовки страниц дашборда (п.1 доработки W1): после вендоринга библиотек
# в app/static/vendor страница не требует внешних ресурсов, поэтому всё
# ограничено 'self'. Единственное исключение — img-src data:: SVG-стрелки
# select/radio из bootstrap.min.css (см. ADR-025). WaveSurfer удалён в п.2:
# плеер — собственный WebAudio (/static/js/player.js, decodeAudioData),
# инлайн-стили и fetch(blob:) больше не нужны — style-src/connect-src строгие.
# script-src — строго 'self' без unsafe-inline/unsafe-eval (см. тест).
# frame-ancestors 'none' — защита от clickjacking действий дашборда.
DASHBOARD_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; media-src 'self'; connect-src 'self'; "
        "frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def _dashboard_response(path: str, missing_text: str) -> HTMLResponse:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), headers=DASHBOARD_SECURITY_HEADERS)
    return HTMLResponse(
        f"<h3>{missing_text}</h3>", status_code=404, headers=DASHBOARD_SECURITY_HEADERS
    )


@router.get("/", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
@router.get("/ui", response_class=HTMLResponse)
@router.get("/dashboard-modern", response_class=HTMLResponse)
@router.get("/ui-modern", response_class=HTMLResponse)
async def serve_dashboard():
    """Страница звонков: список, плеер, расшифровка, разбор."""
    return _dashboard_response(DASHBOARD_PATH, "Dashboard file not found")


@router.get("/voice", response_class=HTMLResponse)
async def serve_voice():
    """Страница голосового поиска: запрос, история, кластеры."""
    return _dashboard_response(VOICE_PATH, "Voice page file not found")


@router.get("/dashboard-classic", response_class=HTMLResponse)
@router.get("/ui-classic", response_class=HTMLResponse)
async def serve_classic_dashboard():
    """Удалена в W1.2 (консолидация): редирект на единый дашборд."""
    return RedirectResponse(url="/dashboard", status_code=303, headers=DASHBOARD_SECURITY_HEADERS)
