import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.main import app


@pytest.mark.asyncio
async def test_auth_dev_token_in_production(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "dev_token", None)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Request with dev-token when not set should fail
        resp = await client.get(
            "/v1/calls?namespace=test_msk", headers={"Authorization": "Bearer dev-token"}
        )
        assert resp.status_code == 401

        # Request with debug token should fail in prod
        monkeypatch.setattr(settings, "debug", True)
        resp2 = await client.get(
            "/v1/calls?namespace=test_msk", headers={"Authorization": "Bearer debug-token"}
        )
        assert resp2.status_code == 401


@pytest.mark.asyncio
async def test_auth_dev_token_in_dev(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "dev_token", "dev-token-xyz")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/v1/calls?namespace=test_msk", headers={"Authorization": "Bearer dev-token-xyz"}
        )
        assert resp.status_code == 200

        # Токены в URL запрещены: они попадают в access-логи и историю браузера.
        resp_query = await client.get("/v1/calls?namespace=test_msk&token=dev-token-xyz")
        assert resp_query.status_code == 401


@pytest.mark.asyncio
async def test_tenant_namespace_isolation(monkeypatch):
    from app.core.security import hash_token

    token_a = "token-tenant-a-12345"
    hash_a = hash_token(token_a)
    settings = get_settings()
    monkeypatch.setattr(settings, "api_tokens_hashed", f"{hash_a}:read+write:tenant-a")
    monkeypatch.setattr(settings, "dev_token", None)
    monkeypatch.setattr(settings, "debug", False)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Accessing own namespace should succeed (200)
        resp_own = await client.get(
            "/v1/calls?namespace=tenant-a", headers={"Authorization": f"Bearer {token_a}"}
        )
        assert resp_own.status_code == 200

        # Accessing another namespace should be rejected with 403 Forbidden
        resp_foreign = await client.get(
            "/v1/calls?namespace=tenant-b", headers={"Authorization": f"Bearer {token_a}"}
        )
        assert resp_foreign.status_code == 403
        data = resp_foreign.json()
        assert data.get("error") == "forbidden_namespace"


@pytest.mark.asyncio
async def test_token_revocation_enforced(monkeypatch):
    from app.core.security import hash_token, revoke_token, unrevoke_token

    token_x = "token-to-revoke-99999"
    hash_x = hash_token(token_x)
    settings = get_settings()
    monkeypatch.setattr(settings, "api_tokens_hashed", f"{hash_x}:read+write:tenant-x")
    monkeypatch.setattr(settings, "dev_token", None)
    monkeypatch.setattr(settings, "debug", False)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp_before = await client.get(
            "/v1/calls?namespace=tenant-x", headers={"Authorization": f"Bearer {token_x}"}
        )
        assert resp_before.status_code == 200

        # Revoke token
        revoke_token(hash_x)
        try:
            resp_after = await client.get(
                "/v1/calls?namespace=tenant-x", headers={"Authorization": f"Bearer {token_x}"}
            )
            assert resp_after.status_code == 401
            assert resp_after.json().get("error") == "token_revoked"
        finally:
            unrevoke_token(hash_x)


@pytest.mark.asyncio
async def test_token_revocation_redis_sync(monkeypatch):
    """When a token is revoked in Redis by another worker, verify_auth rejects it."""
    from unittest.mock import AsyncMock, MagicMock

    from app.core.security import hash_token, unrevoke_token

    token_y = "token-revoked-via-redis-55555"
    hash_y = hash_token(token_y)
    settings = get_settings()
    monkeypatch.setattr(settings, "api_tokens_hashed", f"{hash_y}:read+write:tenant-y")
    monkeypatch.setattr(settings, "dev_token", None)
    monkeypatch.setattr(settings, "debug", False)

    fake_redis = MagicMock()

    async def fake_sismember(key, member):
        return member == hash_y

    fake_redis.sismember = fake_sismember

    mock_queue = MagicMock()
    mock_queue.get_client = AsyncMock(return_value=fake_redis)
    monkeypatch.setattr("app.core.security.get_queue", lambda: mock_queue)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        try:
            resp = await client.get(
                "/v1/calls?namespace=tenant-y", headers={"Authorization": f"Bearer {token_y}"}
            )
            assert resp.status_code == 401
            assert resp.json().get("error") == "token_revoked"
        finally:
            unrevoke_token(hash_y)
