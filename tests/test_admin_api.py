import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("DEV_TOKEN", "dev-token")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("S3_ENDPOINT_URL", "http://localhost:9000")
os.environ.setdefault("CALLBACK_SIGNING_SECRET", "test-secret-key-32-characters!!!")

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
async def test_delete_data_by_period_with_tz_aware_dates():
    """Удаление по периоду с aware-датами (+00:00) не даёт 500.

    Регрессия: сравнение offset-aware параметра с naive created_at роняло
    Postgres (asyncpg: can't subtract offset-naive and offset-aware).
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        headers = {"Authorization": "Bearer dev-token"}
        response = await ac.delete(
            "/v1/data/delete?namespace=nonexistent_namespace_xyz"
            "&date_from=2026-10-11T00:00:00%2B00:00&date_to=2026-10-12T00:00:00%2B00:00",
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["deleted_calls"] == 0


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


@pytest.mark.asyncio
async def test_admin_gdpr_deletes_voice_history():
    """Verify delete_data endpoint cleans up voice history in Redis for namespace."""
    from unittest.mock import AsyncMock, MagicMock, patch

    fake_redis = MagicMock()
    fake_redis.delete = AsyncMock(return_value=1)
    mock_queue = MagicMock()
    mock_queue.get_client = AsyncMock(return_value=fake_redis)

    with patch("app.queue.redis_streams.get_queue", return_value=mock_queue):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            headers = {"Authorization": "Bearer dev-token"}
            res = await ac.delete("/v1/data/delete?namespace=test_gdpr_ns", headers=headers)
            assert res.status_code == 200
            data = res.json()
            assert "deleted_voice_queries" in data
            fake_redis.delete.assert_called_with("voice:history:test_gdpr_ns")


@pytest.mark.asyncio
async def test_revoked_tokens_cursor_pagination():
    """cursor/next_cursor + кап limit на списке отозванных токенов."""
    from app.core.security import _revoked_token_hashes, hash_token, unrevoke_token

    hashes = set()
    for suffix in ("c8p1", "c8p2", "c8p3"):
        h = hash_token(f"page-test-token-{suffix}")
        hashes.add(h)
        _revoked_token_hashes.add(h)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            headers = {"Authorization": "Bearer dev-token"}
            seen, cursor, guard = set(), None, 0
            while guard < 10:
                qs = "/v1/admin/tokens/revoked?limit=2" + (f"&cursor={cursor}" if cursor else "")
                page = (await ac.get(qs, headers=headers)).json()
                assert len(page["revoked_hashes"]) <= 2
                seen.update(page["revoked_hashes"])
                cursor = page["next_cursor"]
                guard += 1
                if cursor is None:
                    break
            assert hashes <= seen
            bad = await ac.get("/v1/admin/tokens/revoked?limit=101", headers=headers)
            assert bad.status_code == 422
    finally:
        for h in hashes:
            unrevoke_token(h)
