from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.integrations.bitrix24 import format_timeline_comment, parse_bitrix24_webhook
from app.main import app
from app.storage.s3 import S3Storage


def test_bitrix24_webhook_payload_parsing_nested():
    raw_payload = {
        "event": "ONVOXIMPLANTCALLFINISHED",
        "data": {
            "CALL_ID": "vox_call_999",
            "RECORD_URL": "https://telephony.example.com/recordings/call_999.mp3",
            "PORTAL_USER_ID": "42",
            "PHONE_NUMBER": "+79991234567",
            "CALL_DURATION": "125.5",
            "CALL_TYPE": "2",
            "CRM_ENTITY_TYPE": "LEAD",
            "CRM_ENTITY_ID": "55512",
        },
        "auth": {
            "domain": "billboards.bitrix24.ru",
            "application_token": "secret_app_token",
        },
    }

    parsed = parse_bitrix24_webhook(raw_payload)
    assert parsed["event"] == "ONVOXIMPLANTCALLFINISHED"
    assert parsed["external_id"] == "vox_call_999"
    assert parsed["record_url"] == "https://telephony.example.com/recordings/call_999.mp3"
    assert parsed["manager_id"] == "42"
    assert parsed["client_id"] == "+79991234567"
    assert parsed["duration_sec"] == 125.5
    assert parsed["direction"] == "inbound"
    assert parsed["crm_entity_type"] == "lead"
    assert parsed["crm_entity_id"] == "55512"
    assert parsed["portal_domain"] == "billboards.bitrix24.ru"


def test_bitrix24_webhook_payload_parsing_form_bracketed():
    form_payload = {
        "event": "ONVOXIMPLANTCALLFINISHED",
        "data[CALL_ID]": "vox_form_123",
        "data[RECORD_URL]": "https://telephony.example.com/recordings/form_123.wav",
        "data[PORTAL_USER_ID]": "10",
        "data[PHONE_NUMBER]": "+79031112233",
        "data[CALL_DURATION]": "45",
        "data[CALL_TYPE]": "1",
        "data[CRM_ENTITY_TYPE]": "DEAL",
        "data[CRM_ENTITY_ID]": "888",
    }

    parsed = parse_bitrix24_webhook(form_payload)
    assert parsed["external_id"] == "vox_form_123"
    assert parsed["record_url"] == "https://telephony.example.com/recordings/form_123.wav"
    assert parsed["manager_id"] == "10"
    assert parsed["direction"] == "outbound"
    assert parsed["crm_entity_type"] == "deal"
    assert parsed["crm_entity_id"] == "888"


def test_bitrix24_format_timeline_comment():
    analysis = {
        "summary": "Клиент запросил размещение на 3 месяца на МКАД.",
        "inquiry_type": "new_order",
        "call_result": "commercial_proposal_sent",
        "sentiment": "positive",
        "agreements": ["Отправить медиаплан до 15:00"],
        "entities": {
            "locations": ["МКАД", "Балашиха"],
            "surfaces": ["щит 3х6"],
            "budget": "200 000 руб.",
        },
    }
    comment = format_timeline_comment(
        analysis=analysis,
        full_text="Здравствуйте, интересует щит на МКАД.",
        duration_sec=75.0,
    )
    assert "Анализ звонка (APC-Speech AI)" in comment
    assert "new_order" in comment
    assert "commercial_proposal_sent" in comment
    assert "Позитивная" in comment
    assert "Отправить медиаплан до 15:00" in comment
    assert "МКАД, Балашиха" in comment
    assert "200 000 руб." in comment
    assert "Здравствуйте, интересует щит на МКАД." in comment


def test_s3_presigned_url_generation():
    with patch("boto3.client") as mock_boto:
        mock_client = MagicMock()
        mock_client.generate_presigned_url.return_value = (
            "https://s3.example.com/raw-audio/calls/123.wav?X-Amz-Signature=abc"
        )
        mock_boto.return_value = mock_client

        storage = S3Storage()
        url = storage.generate_presigned_url("raw-audio", "calls/123.wav", expires_in_sec=1800)
        assert "https://s3.example.com" in url
        mock_client.generate_presigned_url.assert_called_once_with(
            ClientMethod="get_object",
            Params={"Bucket": "raw-audio", "Key": "calls/123.wav"},
            ExpiresIn=1800,
        )


@pytest.mark.asyncio
async def test_bitrix24_webhook_endpoint():
    with (
        patch("app.api.integrations.get_queue") as mock_get_queue,
        patch("app.api.integrations.get_db") as mock_get_db,
        patch(
            "app.api.integrations.validate_ssrf_url",
            return_value="https://pub.telephony.bitrix24.ru/records/001.mp3",
        ),
    ):
        mock_queue = AsyncMock()
        mock_get_queue.return_value = mock_queue

        mock_db = AsyncMock()
        mock_get_db.return_value = mock_db
        # Mock scalar_one_or_none to return None (no duplicate job)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            payload = {
                "event": "ONVOXIMPLANTCALLFINISHED",
                "data": {
                    "CALL_ID": "b24_test_001",
                    "RECORD_URL": "https://pub.telephony.bitrix24.ru/records/001.mp3",
                    "PORTAL_USER_ID": "15",
                    "PHONE_NUMBER": "+79998887766",
                    "CALL_DURATION": "90",
                    "CRM_ENTITY_TYPE": "DEAL",
                    "CRM_ENTITY_ID": "1001",
                },
            }
            # Provide auth token via query param
            resp = await client.post(
                "/v1/integrations/bitrix24/webhook?token=dev-token&namespace=outdoor_msk",
                json=payload,
            )
            assert resp.status_code == 202
            data = resp.json()
            assert data["status"] == "queued"
            assert "job_id" in data
            assert "call_id" in data
            assert data["status_url"].startswith("/v1/jobs/")


@pytest.mark.asyncio
async def test_bitrix24_webhook_missing_record():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "event": "ONVOXIMPLANTCALLFINISHED",
            "data": {
                "CALL_ID": "empty_call",
            },
        }
        resp = await client.post(
            "/v1/integrations/bitrix24/webhook?token=dev-token&namespace=outdoor_msk",
            json=payload,
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_record_url"
