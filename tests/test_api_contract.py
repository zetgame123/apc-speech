from pathlib import Path

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_health_check_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["version"] == "0.1.0"


def test_static_openapi_covers_runtime_routes_and_key_constraints():
    spec = yaml.safe_load(Path("openapi.yaml").read_text(encoding="utf-8"))
    documented = {
        (method.upper(), path)
        for path, path_item in spec["paths"].items()
        for method in path_item
        if method.lower() in {"get", "post", "put", "patch", "delete"}
    }
    framework_routes = {"/openapi.json", "/docs", "/docs/oauth2-redirect"}
    runtime = {
        (method, route.path)
        for route in app.routes
        for method in getattr(route, "methods", set())
        if route.path not in framework_routes and method != "HEAD"
    }
    assert runtime <= documented

    summary_params = {
        item["name"]: item for item in spec["paths"]["/v1/analytics/summary"]["get"]["parameters"]
    }
    assert "required" not in summary_params["date_from"]
    assert "required" not in summary_params["date_to"]
    assert "inquiry_type" in summary_params

    texts = spec["paths"]["/v1/texts/analyze"]["post"]["requestBody"]["content"]
    texts_schema = texts["application/json"]["schema"]["properties"]["texts"]
    assert texts_schema["maxItems"] == 500


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


@pytest.mark.asyncio
async def test_metrics_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/metrics")
        assert resp.status_code == 200
        assert "python_gc_objects_collected_total" in resp.text


@pytest.mark.asyncio
async def test_analytics_export_csv_endpoints():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = {"Authorization": "Bearer dev-token"}
        # Test standard route
        resp1 = await client.get("/v1/analytics/export_csv?namespace=test_msk", headers=headers)
        assert resp1.status_code == 200
        assert "call_id,external_id" in resp1.text

        # Test dot-alias route
        resp2 = await client.get("/v1/analytics/export.csv?namespace=test_msk", headers=headers)
        assert resp2.status_code == 200
        assert "call_id,external_id" in resp2.text


def test_schema_validation_strict_error():
    """Verify that invalid analysis schema raises ValidationError."""
    from app.llm.port import validate_analysis_schema

    invalid_data = {
        "schema_version": "v1.0",
        # Missing required fields like summary, call_result, sentiment, etc.
    }
    with pytest.raises(Exception):
        validate_analysis_schema(invalid_data)


def test_openapi_all_json_operations_have_examples():
    """Этап D: у каждого JSON requestBody и каждого 2xx JSON-ответа есть example.

    Бинарные (audio) и текстовые (csv, metrics) ответы исключены осознанно.
    """
    import yaml

    with open("openapi.yaml", encoding="utf-8") as f:
        spec = yaml.safe_load(f)
    missing = []
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            if not isinstance(op, dict):
                continue
            body = (op.get("requestBody") or {}).get("content", {}).get("application/json")
            if body is not None and "example" not in body and "examples" not in body:
                missing.append(f"REQ {method.upper()} {path}")
            for code, resp in (op.get("responses") or {}).items():
                if not str(code).startswith("2"):
                    continue
                content = (resp.get("content") or {}).get("application/json")
                if content is not None and "example" not in content and "examples" not in content:
                    missing.append(f"RESP {method.upper()} {path} {code}")
    assert not missing, f"нет example: {missing}"


def test_swagger_docs_enabled_only_outside_production(monkeypatch):
    """Swagger UI на /docs — только вне production (пересборка app с env)."""
    import importlib

    import app.main as main_mod

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("DEBUG", raising=False)
    get_settings = main_mod.get_settings
    get_settings.cache_clear()
    try:
        importlib.reload(main_mod)
        prod_routes = {getattr(r, "path", "") for r in main_mod.app.routes}
        assert "/docs" not in prod_routes
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
        importlib.reload(main_mod)
