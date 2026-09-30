from typing import Any

import httpx

from app.core.logging import get_logger
from app.core.security import validate_ssrf_url

logger = get_logger("bitrix24_integration")


def parse_bitrix24_webhook(raw_data: dict[str, Any]) -> dict[str, Any]:
    """
    Parses incoming Bitrix24 webhook payloads (both JSON and form-urlencoded dictionary representations).
    Handles standard Voximplant telephony webhook fields (ONVOXIMPLANTCALLFINISHED).
    """
    # 1. Check for nested 'data' object or flat keys like 'data[CALL_ID]'
    data_dict = raw_data.get("data") if isinstance(raw_data.get("data"), dict) else {}
    auth_dict = raw_data.get("auth") if isinstance(raw_data.get("auth"), dict) else {}

    def get_val(*keys: str) -> Any:
        for k in keys:
            if k in raw_data and raw_data[k] is not None:
                return raw_data[k]
            if k in data_dict and data_dict[k] is not None:
                return data_dict[k]
            # Check form-encoded pattern like data[KEY]
            bracket_key = f"data[{k}]"
            if bracket_key in raw_data and raw_data[bracket_key] is not None:
                return raw_data[bracket_key]
        return None

    call_id = get_val("CALL_ID", "call_id", "ID", "id")
    record_url = get_val("RECORD_URL", "record_url", "URL", "url")
    portal_user_id = get_val("PORTAL_USER_ID", "portal_user_id", "USER_ID", "user_id")
    phone_number = get_val("PHONE_NUMBER", "phone_number", "caller_id")
    duration = get_val("CALL_DURATION", "duration", "CALL_TIME")
    call_type = get_val("CALL_TYPE", "call_type")

    # CRM entity attachments (Lead / Deal / Contact)
    crm_entity_type = get_val("CRM_ENTITY_TYPE", "crm_entity_type", "ENTITY_TYPE")
    crm_entity_id = get_val("CRM_ENTITY_ID", "crm_entity_id", "ENTITY_ID")
    crm_activity_id = get_val("CRM_ACTIVITY_ID", "crm_activity_id")

    # Portal domain and credentials
    domain = auth_dict.get("domain") or raw_data.get("auth[domain]") or raw_data.get("domain")
    token = (
        auth_dict.get("application_token")
        or raw_data.get("auth[application_token]")
        or raw_data.get("auth_token")
    )

    # Normalize call direction: 1 = outbound, 2 = inbound, 3 = inbound with redirect, etc.
    direction = "inbound"
    if str(call_type) in ["1", "outbound"]:
        direction = "outbound"

    # Duration as float
    duration_sec = None
    if duration is not None:
        try:
            duration_sec = float(duration)
        except (ValueError, TypeError):
            duration_sec = None

    # Normalize CRM entity type (lead / deal / contact)
    if crm_entity_type:
        crm_entity_type = str(crm_entity_type).lower()

    return {
        "event": raw_data.get("event"),
        "external_id": str(call_id) if call_id else None,
        "record_url": str(record_url).strip() if record_url else None,
        "manager_id": str(portal_user_id) if portal_user_id else None,
        "client_id": str(phone_number) if phone_number else None,
        "direction": direction,
        "duration_sec": duration_sec,
        "crm_entity_type": crm_entity_type,
        "crm_entity_id": crm_entity_id,
        "crm_activity_id": crm_activity_id,
        "portal_domain": domain,
        "auth_token": token,
    }


def format_timeline_comment(
    analysis: dict[str, Any],
    full_text: str = "",
    duration_sec: float | None = None,
) -> str:
    """
    Formats structured analysis and transcription into a readable Bitrix24 timeline comment.
    Handles schema-compliant dicts for sentiment, agreements, and entities.
    """
    summary = analysis.get("summary") or "—"
    inquiry_type = analysis.get("inquiry_type") or "—"
    call_result = analysis.get("call_result") or "—"

    sentiment_raw = analysis.get("sentiment")
    if isinstance(sentiment_raw, dict):
        client_sentiment = sentiment_raw.get("client_overall", "neutral")
        manager_tone = sentiment_raw.get("manager_tone", "polite_constructive")
        sentiment_label = client_sentiment
        sentiment_text = f"{client_sentiment} (тон менеджера: {manager_tone})"
    else:
        sentiment_label = str(sentiment_raw or "neutral")
        sentiment_text = sentiment_label

    sentiment_emoji = {
        "positive": "🟢 Позитивная",
        "negative": "🔴 Негативная",
        "conflict": "💥 Конфликтная",
        "neutral": "⚪ Нейтральная",
    }.get(sentiment_label, sentiment_text)

    lines = [
        "📞 [b]Анализ звонка (APC-Speech AI)[/b]",
        f"⏱ [b]Длительность:[/b] {round(duration_sec, 1) if duration_sec else '—'} сек.",
        f"📌 [b]Тип обращения:[/b] {inquiry_type}",
        f"🎯 [b]Итог звонка:[/b] {call_result}",
        f"📊 [b]Тональность:[/b] {sentiment_emoji}",
        f"📝 [b]Краткое резюме:[/b]\n{summary}",
    ]

    agreements = analysis.get("agreements") or []
    if agreements:
        agreements_lines = []
        for a in agreements:
            if isinstance(a, dict):
                who = (
                    "Менеджер"
                    if a.get("who") == "manager"
                    else ("Клиент" if a.get("who") == "client" else "Обе стороны")
                )
                what = a.get("what", "")
                deadline = f" (срок: {a['deadline']})" if a.get("deadline") else ""
                agreements_lines.append(f"  • [{who}] {what}{deadline}")
            else:
                agreements_lines.append(f"  • {a}")
        lines.append("🤝 [b]Договоренности:[/b]\n" + "\n".join(agreements_lines))

    entities = analysis.get("entities") or {}
    notable_entities = []
    cities = entities.get("cities") or entities.get("locations") or []
    formats = entities.get("formats") or entities.get("surfaces") or []
    budget = (
        entities.get("budget_rub")
        if entities.get("budget_rub") is not None
        else entities.get("budget")
    )

    if cities:
        notable_entities.append(f"Локации: {', '.join(cities)}")
    if formats:
        notable_entities.append(f"Форматы: {', '.join(formats)}")
    if budget is not None:
        notable_entities.append(
            f"Бюджет: {budget:,.0f} ₽" if isinstance(budget, (int, float)) else f"Бюджет: {budget}"
        )

    if notable_entities:
        lines.append(
            "🏷 [b]Сущности рекламы:[/b]\n" + "\n".join(f"  • {e}" for e in notable_entities)
        )

    if full_text:
        # Limit preview transcript if extremely long
        preview = full_text if len(full_text) <= 1500 else full_text[:1470] + "..."
        lines.append(f"\n💬 [b]Расшифровка диалога:[/b]\n{preview}")

    return "\n\n".join(lines)


async def post_to_crm_timeline(
    webhook_url: str,
    entity_type: str,
    entity_id: int | str,
    comment: str,
) -> bool:
    """
    Posts a comment to Bitrix24 deal or lead timeline via incoming webhook or REST endpoint.
    Endpoint URL format: https://<domain>/rest/<user_id>/<token>/crm.timeline.comment.add
    """
    if not webhook_url:
        return False

    # SSRF protection
    try:
        validate_ssrf_url(webhook_url)
    except Exception as e:
        logger.error("bitrix24_webhook_ssrf_rejected", url=webhook_url, error=str(e))
        return False

    url = webhook_url
    if not url.endswith("/crm.timeline.comment.add") and not url.endswith("/"):
        url = url.rstrip("/") + "/crm.timeline.comment.add"
    elif url.endswith("/"):
        url = url + "crm.timeline.comment.add"

    payload = {
        "fields": {
            "ENTITY_TYPE": entity_type.lower(),
            "ENTITY_ID": entity_id,
            "COMMENT": comment,
        }
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)
            if resp.is_success:
                logger.info(
                    "bitrix24_timeline_comment_posted", entity_type=entity_type, entity_id=entity_id
                )
                return True
            else:
                logger.warning(
                    "bitrix24_timeline_post_failed",
                    status_code=resp.status_code,
                    response=resp.text[:500],
                )
                return False
    except Exception as e:
        logger.error("bitrix24_timeline_exception", error=str(e))
        return False
