import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_health_check_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["version"] == "1.0.0"


@pytest.mark.asyncio
async def test_text_analysis_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "namespace": "test_msk",
            "texts": [
                "щиты 3х6 в Балашихе",
                "суперсайт на Ленинградском шоссе",
                "сколько стоит размещение",
            ],
        }
        headers = {"Authorization": "Bearer dev-token"}
        resp = await client.post("/v1/texts/analyze", json=payload, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_texts"] == 3
        assert len(data["clusters"]) >= 1


@pytest.mark.asyncio
async def test_auth_unauthorized():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/v1/calls?namespace=test_msk")
        assert resp.status_code == 401
        data = resp.json()
        assert data["error"] == "unauthorized"


@pytest.mark.asyncio
async def test_dashboard_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/dashboard")
        assert resp.status_code == 200
        assert "APC-SPEECH" in resp.text
        assert "audioPlayer" in resp.text
