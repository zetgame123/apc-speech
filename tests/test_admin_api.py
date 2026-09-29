import os

os.environ.setdefault('DATABASE_URL', 'sqlite+aiosqlite:///:memory:')
os.environ.setdefault('DEBUG', 'true')
os.environ.setdefault('DEV_TOKEN', 'dev-token')
os.environ.setdefault('APP_ENV', 'test')
os.environ.setdefault('REDIS_URL', 'redis://localhost:6379/0')
os.environ.setdefault('S3_ENDPOINT_URL', 'http://localhost:9000')
os.environ.setdefault('CALLBACK_SIGNING_SECRET', 'test-secret-key-32-characters!!!')

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_delete_data_unauthorized():
    """No auth token → 401."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.delete("/v1/data/delete?namespace=test")
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_delete_data_requires_admin_scope():
    """Invalid token → 401 (no matching token, so unauthorized)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        headers = {"Authorization": "Bearer some-random-invalid-token"}
        response = await ac.delete("/v1/data/delete?namespace=test", headers=headers)
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_delete_data_no_matches():
    """Admin token + empty namespace → {deleted_calls: 0}."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        headers = {"Authorization": "Bearer dev-token"}
        response = await ac.delete(
            "/v1/data/delete?namespace=nonexistent_namespace_xyz",
            headers=headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["deleted_calls"] == 0
        assert data["deleted_audio_files"] == 0


@pytest.mark.asyncio
async def test_revoke_token_endpoint():
    """Admin can revoke a token hash."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        headers = {"Authorization": "Bearer dev-token"}
        response = await ac.post(
            "/v1/admin/tokens/revoke",
            json={"token_hash": "abc123deadbeef"},
            headers=headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["revoked"] is True


@pytest.mark.asyncio
async def test_list_revoked_tokens_endpoint():
    """Admin can list revoked token hashes."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        headers = {"Authorization": "Bearer dev-token"}
        response = await ac.get("/v1/admin/tokens/revoked", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert "revoked_hashes" in data
        assert isinstance(data["revoked_hashes"], list)
