import os

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["dashboard"])

STATIC_INDEX_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "index.html")
MODERN_DASHBOARD_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "static", "dashboard-modern.html"
)


@router.get("/", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
@router.get("/ui", response_class=HTMLResponse)
async def serve_dashboard():
    if os.path.exists(STATIC_INDEX_PATH):
        with open(STATIC_INDEX_PATH, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h3>Dashboard file not found</h3>", status_code=404)


@router.get("/dashboard-modern", response_class=HTMLResponse)
@router.get("/ui-modern", response_class=HTMLResponse)
async def serve_modern_dashboard():
    """Serve the refreshed dashboard skin while keeping the original UI available."""
    if os.path.exists(MODERN_DASHBOARD_PATH):
        with open(MODERN_DASHBOARD_PATH, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h3>Modern dashboard file not found</h3>", status_code=404)
