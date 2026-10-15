import os

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["dashboard"])

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
STATIC_INDEX_PATH = os.path.join(STATIC_DIR, "index.html")
MODERN_DASHBOARD_PATH = os.path.join(STATIC_DIR, "dashboard-modern.html")
CLASSIC_DASHBOARD_PATH = os.path.join(STATIC_DIR, "index-classic.html")

# Заголовки страниц дашборда (п.1 доработки W1): после вендоринга библиотек
# в app/static/vendor страница не требует внешних ресурсов, поэтому
# script-src/style-src ограничены 'self'. media-src включает blob: — аудио
# грузится через fetch с Authorization в blob-URL. frame-ancestors 'none' —
# защита от clickjacking действий дашборда.
DASHBOARD_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def _dashboard_response(path: str, missing_text: str, missing_status: int = 404) -> HTMLResponse:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), headers=DASHBOARD_SECURITY_HEADERS)
    return HTMLResponse(
        f"<h3>{missing_text}</h3>", status_code=missing_status, headers=DASHBOARD_SECURITY_HEADERS
    )


@router.get("/", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
@router.get("/ui", response_class=HTMLResponse)
@router.get("/dashboard-modern", response_class=HTMLResponse)
@router.get("/ui-modern", response_class=HTMLResponse)
async def serve_dashboard():
    """Serve the modern improved dashboard as the primary interface."""
    target_path = (
        MODERN_DASHBOARD_PATH if os.path.exists(MODERN_DASHBOARD_PATH) else STATIC_INDEX_PATH
    )
    return _dashboard_response(target_path, "Dashboard file not found")


@router.get("/dashboard-classic", response_class=HTMLResponse)
@router.get("/ui-classic", response_class=HTMLResponse)
async def serve_classic_dashboard():
    """Serve the classic legacy dashboard if needed for comparison."""
    target_path = (
        CLASSIC_DASHBOARD_PATH if os.path.exists(CLASSIC_DASHBOARD_PATH) else STATIC_INDEX_PATH
    )
    return _dashboard_response(target_path, "Classic dashboard file not found")
