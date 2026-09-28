import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.core.config import get_settings

@pytest.mark.asyncio
async def test_auth_dev_token_in_production(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "dev_token", None)
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Request with dev-token when not set should fail
        resp = await client.get("/v1/calls?namespace=test_msk", headers={"Authorization": "Bearer dev-token"})
        assert resp.status_code == 401
        
        # Request with debug token should fail in prod
        monkeypatch.setattr(settings, "debug", True)
        resp2 = await client.get("/v1/calls?namespace=test_msk", headers={"Authorization": "Bearer debug-token"})
        assert resp2.status_code == 401

@pytest.mark.asyncio
async def test_auth_dev_token_in_dev(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "dev_token", "dev-token-xyz")
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/v1/calls?namespace=test_msk", headers={"Authorization": "Bearer dev-token-xyz"})
        assert resp.status_code == 200 # assuming this route exists and returns 200 or at least doesn't return 401
