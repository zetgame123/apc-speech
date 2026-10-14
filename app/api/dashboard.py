import os

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["dashboard"])

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
STATIC_INDEX_PATH = os.path.join(STATIC_DIR, "index.html")
MODERN_DASHBOARD_PATH = os.path.join(STATIC_DIR, "dashboard-modern.html")
CLASSIC_DASHBOARD_PATH = os.path.join(STATIC_DIR, "index-classic.html")


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
    if os.path.exists(target_path):
        with open(target_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h3>Dashboard file not found</h3>", status_code=404)


@router.get("/dashboard-classic", response_class=HTMLResponse)
@router.get("/ui-classic", response_class=HTMLResponse)
async def serve_classic_dashboard():
    """Serve the classic legacy dashboard if needed for comparison."""
    target_path = (
        CLASSIC_DASHBOARD_PATH if os.path.exists(CLASSIC_DASHBOARD_PATH) else STATIC_INDEX_PATH
    )
    if os.path.exists(target_path):
        with open(target_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h3>Classic dashboard file not found</h3>", status_code=404)
