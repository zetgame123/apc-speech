from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.models import Analysis, Call
from app.main import app
from app.pii.masker import get_pii_masker


def test_get_pii_masker_returns_isolated_instances():
    """Each request/call must get an isolated PiiMasker so phone/name caches never leak across tenants."""
    m1 = get_pii_masker()
    m2 = get_pii_masker()
    assert m1 is not m2
    assert m1.mask_text("Звоните +7 999 111-22-33") == "Звоните [ТЕЛЕФОН_1]"
    assert m2.mask_text("Звоните +7 999 444-55-66") == "Звоните [ТЕЛЕФОН_1]"


@pytest.mark.asyncio
async def test_analytics_summary_and_streaming_csv(monkeypatch):
    from tests.conftest import TestingSessionLocal

    fake_redis = MagicMock()
    fake_redis.sismember = AsyncMock(return_value=False)
    mock_queue = MagicMock()
    mock_queue.get_client = AsyncMock(return_value=fake_redis)
    monkeypatch.setattr("app.core.security.get_queue", lambda: mock_queue)

    async with TestingSessionLocal() as db:
        c1 = Call(
            id="an-call-1",
            namespace="ns_an",
            manager_id="mgr1",
            duration_sec=60.0,
            created_at=datetime(2026, 10, 12, 10, 0, 0),
        )
        c2 = Call(
            id="an-call-2",
            namespace="ns_an",
            manager_id="mgr2",
            duration_sec=120.0,
            created_at=datetime(2026, 10, 19, 10, 0, 0),
        )
        a1 = Analysis(
            id="an-1",
            call_id="an-call-1",
            namespace="ns_an",
            inquiry_type="pricing",
            call_result="proposal_sent",
            summary="Запрос цены на щиты в Москве",
            entities={"cities": ["Москва"], "formats": ["3×6"]},
            manager_checklist={
                "greeting": True,
                "need_identification": True,
                "options_offered": False,
                "next_step_named": True,
                "contacts_taken": True,
                "score": 80,
            },
        )
        a2 = Analysis(
            id="an-2",
            call_id="an-call-2",
            namespace="ns_an",
            inquiry_type="availability",
            call_result="callback_scheduled",
            summary="Проверка занятости в Химках",
            entities={"cities": ["Москва", "Химки"], "formats": ["3×6", "ситиборд"]},
            manager_checklist={
                "greeting": True,
                "need_identification": False,
                "options_offered": True,
                "next_step_named": False,
                "contacts_taken": False,
                "score": 40,
            },
        )
        db.add_all([c1, c2, a1, a2])
        await db.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        headers = {"Authorization": "Bearer dev-token"}

        resp = await ac.get("/v1/analytics/summary?namespace=ns_an", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_calls"] == 2
        assert data["inquiry_distribution"] == {"pricing": 1, "availability": 1}
        assert data["top_entities"]["cities"][0] == {"name": "Москва", "count": 2}
        assert data["top_entities"]["formats"][0] == {"name": "3×6", "count": 2}
        assert data["checklist_averages"]["average_score"] == 60.0
        assert data["checklist_averages"]["greeting_pct"] == 100.0
        assert data["checklist_averages"]["need_identification_pct"] == 50.0
        assert len(data["weekly_trends"]) == 2

        # Filtered by inquiry_type
        resp_f = await ac.get(
            "/v1/analytics/summary?namespace=ns_an&inquiry_type=pricing", headers=headers
        )
        assert resp_f.status_code == 200
        assert resp_f.json()["total_calls"] == 1
        assert resp_f.json()["inquiry_distribution"] == {"pricing": 1}

        # Streaming CSV export
        csv_resp = await ac.get("/v1/analytics/export_csv?namespace=ns_an", headers=headers)
        assert csv_resp.status_code == 200
        assert "text/csv" in csv_resp.headers["content-type"]
        lines = csv_resp.text.strip().splitlines()
        assert len(lines) == 3
        assert "an-call-1" in csv_resp.text
        assert "an-call-2" in csv_resp.text


def test_contextual_outdoor_term_fixes_without_breaking_billing():
    from app.asr.normalizer import get_normalizer

    norm = get_normalizer()
    assert norm.normalize("нужны рекламные считы в Химках") == "нужны рекламные щиты в Химках"
    assert norm.normalize("свободные счёты три на шесть") == "свободные щиты 3×6"
    assert norm.normalize("счеты три на шесть на МКАД") == "щиты 3×6 на МКАД"
    # Ordinary billing phrases must remain untouched
    assert "счёт" in norm.normalize("прошу оплатить счёт до пятницы")


@pytest.mark.asyncio
async def test_list_calls_category_filter():
    from tests.conftest import TestingSessionLocal

    async with TestingSessionLocal() as db:
        c1 = Call(id="cat-call-1", namespace="ns_cat")
        c2 = Call(id="cat-call-2", namespace="ns_cat")
        a1 = Analysis(
            id="cat-an-1",
            call_id="cat-call-1",
            namespace="ns_cat",
            inquiry_type="pricing",
            entities={"product_category": "недвижимость"},
        )
        a2 = Analysis(
            id="cat-an-2",
            call_id="cat-call-2",
            namespace="ns_cat",
            inquiry_type="pricing",
            entities={"product_category": "авто"},
        )
        db.add_all([c1, c2, a1, a2])
        await db.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            "/v1/calls?namespace=ns_cat&category=недвижимость",
            headers={"Authorization": "Bearer dev-token"},
        )
        assert resp.status_code == 200
        ids = [item["call_id"] for item in resp.json()["items"]]
        assert ids == ["cat-call-1"]
