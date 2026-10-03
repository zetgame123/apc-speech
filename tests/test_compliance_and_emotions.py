from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.models import Call, TranscriptSegment
from app.llm.local_adapter import compute_compliance_and_violations, compute_emotion_breakdown
from app.llm.port import (
    EmotionsBreakdown,
    Entities,
    ManagerChecklist,
    Sentiment,
    StructuredAnalysis,
    validate_analysis_schema,
)
from app.main import app
from tests.conftest import TestingSessionLocal


def test_compliance_and_violations_full_pass():
    """Verify that a compliant call gets 100% score, 0 violations, and positive praise."""
    checklist = {
        "greeting": True,
        "need_identification": True,
        "options_offered": True,
        "next_step_named": True,
        "contacts_taken": True,
        "score": 5,
    }

    res = compute_compliance_and_violations(checklist, manager_tone="polite_constructive")

    assert res["compliance_score"] == 100.0
    assert len(res["violations"]) == 0
    assert len(res["passed_rules"]) == 6  # 5 items + business etiquette
    assert len(res["recommendations"]) == 1
    assert "соблюдены" in res["recommendations"][0]


def test_compliance_and_violations_penalties():
    """Verify that missed items, forbidden words, and defensive tone trigger violations and lower compliance score."""
    checklist = {
        "greeting": False,
        "need_identification": False,
        "options_offered": False,
        "next_step_named": False,
        "contacts_taken": False,
        "forbidden_words_found": ["отвали", "не знаю"],
        "score": 0,
    }

    res = compute_compliance_and_violations(checklist, manager_tone="rude")

    assert res["compliance_score"] == 0.0
    assert len(res["violations"]) >= 6  # 5 checklist items + forbidden words
    assert any(
        "не знаю" in v.lower() or "отвали" in v.lower() or "лексики" in v.lower()
        for v in res["violations"]
    )
    assert len(res["recommendations"]) >= 2


def test_compute_emotion_breakdown():
    """Verify emotion percentage calculation sums to 100% and reflects transcript content."""
    res_pos = compute_emotion_breakdown(
        client_text="Большое спасибо, всё супер, великолепно, договорились!",
        manager_text="Пожалуйста, рад помочь, хорошего дня!",
        client_overall="positive",
        manager_tone="polite_constructive",
    )

    assert res_pos["client_emotion"] == "enthusiasm_joy"
    assert res_pos["manager_emotion"] == "enthusiastic_helpful"
    bd_pos = res_pos["emotions_breakdown"]
    assert bd_pos["positive_pct"] > 50
    assert (
        round(bd_pos["positive_pct"] + bd_pos["neutral_pct"] + bd_pos["negative_pct"], 1) == 100.0
    )

    # Negative call
    res_neg = compute_emotion_breakdown(
        client_text="Ужасно, это кошмар, бесит, претензия, верните деньги!",
        manager_text="Ничего не знаю, отстаньте.",
        client_overall="negative",
        manager_tone="rude",
    )
    assert res_neg["client_emotion"] == "irritation_anger"
    assert res_neg["manager_emotion"] == "tense_defensive"
    bd_neg = res_neg["emotions_breakdown"]
    assert bd_neg["negative_pct"] > 50
    assert (
        round(bd_neg["positive_pct"] + bd_neg["neutral_pct"] + bd_neg["negative_pct"], 1) == 100.0
    )


def test_structured_analysis_schema_validation_with_new_fields():
    """Verify that StructuredAnalysis with new compliant/emotion fields passes JSON Schema Draft 2020-12 validation."""
    analysis = StructuredAnalysis(
        summary="Клиент обратился по поводу размещения билборда 3х6.",
        inquiry_type="pricing",
        call_result="success_deal",
        entities=Entities(
            cities=["Москва"], formats=["щит 3х6"], budget_rub=150000, quantity=2, period="март"
        ),
        sentiment=Sentiment(
            overall="positive",
            client_emotion="enthusiasm_joy",
            manager_emotion="calm_professional",
            emotions_breakdown=EmotionsBreakdown(
                positive_pct=70.0, neutral_pct=30.0, negative_pct=0.0
            ),
        ),
        manager_checklist=ManagerChecklist(
            greeting=True,
            need_identification=True,
            options_offered=True,
            next_step_named=True,
            contacts_taken=True,
            score=5,
            compliance_score=95.0,
            violations=[],
            passed_rules=["Приветствие", "Выявление потребности"],
            recommendations=["Продолжать в том же духе"],
        ),
        deterministic_speech_metrics={
            "manager_speech_share": 0.55,
            "client_speech_share": 0.40,
            "interruptions_count": 0,
            "silence_duration_sec": 1.2,
            "manager_wpm": 125.0,
            "client_wpm": 115.0,
        },
    )

    data = analysis.model_dump()
    # Validate against schemas/analysis_schema_v1.json
    validate_analysis_schema(data)
    assert data["sentiment"]["client_emotion"] == "enthusiasm_joy"
    assert data["manager_checklist"]["compliance_score"] == 95.0


def test_schema_validates_neutral_manager_tone():
    """Verify that manager_tone='neutral' validates cleanly against JSON schema."""
    analysis = StructuredAnalysis(
        summary="Монолог клиента или нейтральный диалог.",
        inquiry_type="other",
        call_result="info_provided",
        entities=Entities(cities=["Москва"], formats=["щит 3х6"]),
        sentiment=Sentiment(
            client_overall="neutral",
            manager_tone="neutral",
        ),
        manager_checklist=ManagerChecklist(),
        deterministic_speech_metrics={
            "manager_speech_share": 0.0,
            "client_speech_share": 1.0,
            "interruptions_count": 0,
            "silence_duration_sec": 0.0,
            "manager_wpm": 0.0,
            "client_wpm": 120.0,
        },
    )
    data = analysis.model_dump()
    validate_analysis_schema(data)
    assert data["sentiment"]["manager_tone"] == "neutral"


@pytest.mark.asyncio
async def test_llm_adapter_refine_speaker_roles_monologue_collapse(monkeypatch):
    """Verify that when LLM returns null speakers (monologue), refine_speaker_roles collapses all segments to SPEAKER_0."""
    from unittest.mock import AsyncMock, MagicMock

    from app.llm.local_adapter import LocalLlamaCppAdapter

    adapter = LocalLlamaCppAdapter()
    segments = [
        {
            "start": 0.0,
            "end": 2.0,
            "text": "Здравствуйте, меня интересует щит.",
            "speaker_label": "SPEAKER_0",
        },
        {
            "start": 2.5,
            "end": 5.0,
            "text": "В районе Балашихи на октябрь.",
            "speaker_label": "SPEAKER_1",
        },
    ]

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(
        return_value={"choices": [{"message": {"content": '{"manager": null, "client": null}'}}]}
    )

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    monkeypatch.setattr("httpx.AsyncClient", MagicMock(return_value=mock_client))

    refined = await adapter.refine_speaker_roles(segments)
    spk_labels = {s["speaker_label"] for s in refined}
    assert len(spk_labels) == 1
    assert "SPEAKER_0" in spk_labels
    assert all(s["speaker_role"] == "client" for s in refined)


@pytest.mark.asyncio
async def test_transcript_download_headers():
    """Verify that GET /v1/calls/{id}/transcript returns Content-Disposition attachment headers."""
    call_id = "test-call-42"
    namespace = "test_msk"

    async with TestingSessionLocal() as session:
        call = Call(
            id=call_id,
            namespace=namespace,
            external_id="ext-42",
            direction="inbound",
            duration_sec=12.5,
            created_at=datetime.now(timezone.utc),
        )
        session.add(call)

        seg = TranscriptSegment(
            call_id=call_id,
            namespace=namespace,
            segment_order=0,
            speaker_label="SPEAKER_0",
            speaker_role="manager",
            start_time=0.0,
            end_time=2.0,
            text_original="Добрый день!",
            text_masked="Добрый день!",
            confidence=0.98,
        )
        session.add(seg)
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = {"Authorization": "Bearer dev-token"}

        # Text format
        resp_txt = await client.get(
            f"/v1/calls/{call_id}/transcript?namespace={namespace}&format=text", headers=headers
        )
        assert resp_txt.status_code == 200
        assert (
            resp_txt.headers.get("content-disposition")
            == f'attachment; filename="transcript_{call_id}.txt"'
        )
        assert "[manager] Добрый день!" in resp_txt.text

        # SRT format
        resp_srt = await client.get(
            f"/v1/calls/{call_id}/transcript?namespace={namespace}&format=srt", headers=headers
        )
        assert resp_srt.status_code == 200
        assert (
            resp_srt.headers.get("content-disposition")
            == f'attachment; filename="transcript_{call_id}.srt"'
        )
        assert "00:00:00,000 --> 00:00:02,000" in resp_srt.text
        assert "[manager] Добрый день!" in resp_srt.text

        # VTT format
        resp_vtt = await client.get(
            f"/v1/calls/{call_id}/transcript?namespace={namespace}&format=vtt", headers=headers
        )
        assert resp_vtt.status_code == 200
        assert (
            resp_vtt.headers.get("content-disposition")
            == f'attachment; filename="transcript_{call_id}.vtt"'
        )
        assert "WEBVTT" in resp_vtt.text
