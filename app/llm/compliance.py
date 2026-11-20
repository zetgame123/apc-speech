from typing import Any

from app.llm.rule_extractors import (
    coerce_budget,
    coerce_quantity,
)


def compute_compliance_and_violations(
    cl: dict[str, Any], manager_tone: str = "polite_constructive"
) -> dict[str, Any]:
    """Calculates integral compliance score (0-100), violations, passed rules, and coaching advice."""
    greeting = bool(cl.get("greeting", False))
    need_id = bool(cl.get("need_identification", False))
    options = bool(cl.get("options_offered", False))
    next_step = bool(cl.get("next_step_named", False))
    contacts = bool(cl.get("contacts_taken", False))
    forbidden = cl.get("forbidden_words_found") or []

    # Weighted checklist score out of 100
    base = (
        (15 if greeting else 0)
        + (25 if need_id else 0)
        + (25 if options else 0)
        + (20 if next_step else 0)
        + (15 if contacts else 0)
    )
    penalty = len(forbidden) * 20
    if manager_tone == "rude":
        penalty += 30
    elif manager_tone == "indifferent":
        penalty += 10

    compliance_score = max(0, min(100, base - penalty))
    cl["compliance_score"] = compliance_score

    violations = []
    passed_rules = []
    recommendations = []

    if greeting:
        passed_rules.append("Приветствие и представление компании")
    else:
        violations.append("Менеджер не поприветствовал собеседника и не представил компанию")
        recommendations.append(
            "Начинайте диалог с четкого приветствия, своего имени и названия компании."
        )

    if need_id:
        passed_rules.append("Выявление потребности клиента")
    else:
        violations.append("Не выявлены ключевые потребности и критерии клиента")
        recommendations.append(
            "Задавайте открытые уточняющие вопросы о локациях, форматах и сроках размещения."
        )

    if options:
        passed_rules.append("Презентация рекламных форматов и свободных слотов")
    else:
        violations.append("Не предложены конкретные форматы или локации щитов")
        recommendations.append(
            "Предлагайте клиенту минимум 2-3 альтернативных формата или локации."
        )

    if next_step:
        passed_rules.append("Договоренность о следующем шаге")
    else:
        violations.append("Не зафиксирован следующий шаг взаимодействия (дедлайн, КП, встреча)")
        recommendations.append(
            "Обязательно согласуйте следующий шаг с точным дедлайном до завершения разговора."
        )

    if contacts:
        passed_rules.append("Фиксация контактов для связи")
    else:
        violations.append("Не запрошены/не подтверждены контактные данные клиента")
        recommendations.append(
            "Уточняйте e-mail или мессенджер для отправки коммерческого предложения."
        )

    if forbidden:
        for fw in forbidden:
            violations.append(f"Использование недопустимой лексики: '{fw}'")
        recommendations.append(
            "Исключите слова-паразиты и грубые обороты, соблюдайте деловой этикет."
        )
    else:
        passed_rules.append("Соблюдение делового этикета (нет запрещенных слов)")

    if not recommendations:
        recommendations.append(
            "Отличная работа с клиентом! Стандарты обслуживания полностью соблюдены."
        )

    cl["violations"] = violations
    cl["passed_rules"] = passed_rules
    cl["recommendations"] = recommendations
    return cl


def compute_emotion_breakdown(
    client_text: str,
    manager_text: str,
    client_overall: str = "neutral",
    manager_tone: str = "polite_constructive",
) -> dict[str, Any]:
    """Extracts fine-grained emotions and emotion distribution percentages."""
    c_low = client_text.lower()
    m_low = manager_text.lower()

    joy_kws = [
        "спасибо",
        "отлично",
        "прекрасно",
        "супер",
        "здорово",
        "замечательно",
        "договорились",
        "рад",
        "устраивает",
        "хорошо",
    ]
    anger_kws = [
        "дорого",
        "ужасно",
        "плохо",
        "бесит",
        "раздражает",
        "претензия",
        "возмущен",
        "отказ",
        "нет",
        "не устраивает",
    ]
    doubt_kws = ["сомневаюсь", "не уверен", "подумаю", "не знаю", "может быть", "сложно", "вопрос"]
    urgency_kws = ["срочно", "горим", "быстрее", "сегодня же", "дедлайн"]

    joy_count = sum(1 for kw in joy_kws if kw in c_low)
    anger_count = sum(1 for kw in anger_kws if kw in c_low)
    doubt_count = sum(1 for kw in doubt_kws if kw in c_low)
    urgency_count = sum(1 for kw in urgency_kws if kw in c_low)

    if anger_count > 0 or client_overall in ["negative", "conflict"]:
        client_emotion = "irritation_anger"
    elif joy_count > 0 or client_overall == "positive":
        client_emotion = "enthusiasm_joy"
    elif urgency_count > 0:
        client_emotion = "urgency_anxiety"
    elif doubt_count > 0:
        client_emotion = "doubt_skepticism"
    else:
        client_emotion = "neutral_business"

    if manager_tone == "rude":
        manager_emotion = "tense_defensive"
    elif manager_tone == "indifferent":
        manager_emotion = "indifferent_bored"
    elif any(kw in m_low for kw in joy_kws):
        manager_emotion = "enthusiastic_helpful"
    else:
        manager_emotion = "calm_professional"

    total_signals = joy_count + anger_count + doubt_count + 1
    pos_pct = round((joy_count / total_signals) * 100, 1)
    neg_pct = round((anger_count / total_signals) * 100, 1)
    neu_pct = round(max(0.0, 100.0 - pos_pct - neg_pct), 1)

    return {
        "client_emotion": client_emotion,
        "manager_emotion": manager_emotion,
        "emotions_breakdown": {
            "positive_pct": pos_pct,
            "neutral_pct": neu_pct,
            "negative_pct": neg_pct,
        },
    }


WHO_MAP = {
    "client": "client",
    "клиент": "client",
    "клиента": "client",
    "заказчик": "client",
    "покупатель": "client",
    "manager": "manager",
    "менеджер": "manager",
    "оператор": "manager",
    "компания": "manager",
    "агентство": "manager",
    "both": "both",
    "оба": "both",
    "обе стороны": "both",
    "вместе": "both",
}

INQUIRY_MAP = {
    "продажи": "pricing",
    "продажа": "pricing",
    "цена": "pricing",
    "цены": "pricing",
    "стоимость": "pricing",
    "расчет": "pricing",
    "прайс": "pricing",
    "наличие": "availability",
    "свободные слоты": "availability",
    "поиск": "inventory_search",
    "подбор": "inventory_search",
    "характеристики": "specs",
    "статус": "order_status",
    "статус заказа": "order_status",
    "производство": "production",
    "печать": "production",
    "документы": "documents_billing",
    "бухгалтерия": "documents_billing",
    "счет": "documents_billing",
    "счета": "documents_billing",
    "жалоба": "complaint",
    "претензия": "complaint",
    "партнерство": "partnership_supply",
    "сотрудничество": "partnership_supply",
    "спам": "spam_irrelevant",
    "general": "other",
    "общий": "other",
    "прочее": "other",
}

CALL_RESULT_MAP = {
    "продажи": "success_deal",
    "сделка": "success_deal",
    "успех": "success_deal",
    "договорились": "success_deal",
    "следующий шаг": "follow_up_scheduled",
    "кп": "follow_up_scheduled",
    "информация": "info_provided",
    "консультация": "info_provided",
    "отказ": "client_declined",
    "спам": "unclear_spam",
}

VALID_CALL_RESULTS = {
    "success_deal",
    "follow_up_scheduled",
    "info_provided",
    "client_declined",
    "unclear_spam",
}

CLIENT_SENTIMENT_MAP = {
    "positive": "positive",
    "позитивный": "positive",
    "положительный": "positive",
    "neutral": "neutral",
    "нейтральный": "neutral",
    "спокойный": "neutral",
    "деловой": "neutral",
    "negative": "negative",
    "негативный": "negative",
    "отрицательный": "negative",
    "conflict": "conflict",
    "конфликтный": "conflict",
    "конфликт": "conflict",
}

TONE_MAP = {
    "polite_constructive": "polite_constructive",
    "вежливый": "polite_constructive",
    "вежливый_конструктивный": "polite_constructive",
    "конструктивный": "polite_constructive",
    "деловой": "polite_constructive",
    "neutral": "neutral",
    "нейтральный": "neutral",
    "спокойный": "neutral",
    "indifferent": "indifferent",
    "равнодушный": "indifferent",
    "безразличный": "indifferent",
    "пассивный": "indifferent",
    "rude": "rude",
    "грубый": "rude",
    "агрессивный": "rude",
    "невежливый": "rude",
}


def normalize_analysis_dict(parsed: dict[str, Any], transcript: str = "") -> dict[str, Any]:
    """Ensures that all fields in the analysis dictionary conform to the strict JSON schema and enum slugs."""
    from app.llm.port import get_allowed_taxonomy_categories

    # 1. inquiry_type
    if "inquiry_type" in parsed:
        inq_clean = str(parsed["inquiry_type"]).strip().lower()
        if inq_clean in INQUIRY_MAP:
            parsed["inquiry_type"] = INQUIRY_MAP[inq_clean]
        elif parsed["inquiry_type"] not in get_allowed_taxonomy_categories():
            parsed["inquiry_type"] = "other"
    else:
        parsed["inquiry_type"] = "other"

    # 2. call_result
    if "call_result" in parsed:
        cr_clean = str(parsed["call_result"]).strip().lower()
        if cr_clean in CALL_RESULT_MAP:
            parsed["call_result"] = CALL_RESULT_MAP[cr_clean]
        elif parsed["call_result"] not in VALID_CALL_RESULTS:
            parsed["call_result"] = "info_provided"
    else:
        parsed["call_result"] = "info_provided"

    # 3. entities
    if not isinstance(parsed.get("entities"), dict):
        parsed["entities"] = {"cities": [], "formats": []}
    else:
        if not isinstance(parsed["entities"].get("cities"), list):
            parsed["entities"]["cities"] = []
        if not isinstance(parsed["entities"].get("formats"), list):
            parsed["entities"]["formats"] = []
        # Мягкое приведение числовых полей (шаг 1 robustness Qwen):
        # модель любит класть строки ("6", "месяц") вместо integer/null.
        if "quantity" in parsed["entities"]:
            parsed["entities"]["quantity"] = coerce_quantity(parsed["entities"]["quantity"])
        if "budget_rub" in parsed["entities"]:
            parsed["entities"]["budget_rub"] = coerce_budget(parsed["entities"]["budget_rub"])

    # 4. agreements
    if not isinstance(parsed.get("agreements"), list):
        parsed["agreements"] = []
    else:
        for agr in parsed["agreements"]:
            if isinstance(agr, dict):
                raw_who = str(agr.get("who", "manager")).strip().lower()
                agr["who"] = WHO_MAP.get(
                    raw_who,
                    "client" if "клиент" in raw_who else ("both" if "об" in raw_who else "manager"),
                )
                if not agr.get("what"):
                    agr["what"] = "Договоренность по обращению"
                if "deadline" not in agr:
                    agr["deadline"] = None

    # 5. sentiment
    if not isinstance(parsed.get("sentiment"), dict):
        parsed["sentiment"] = {
            "client_overall": "neutral",
            "manager_tone": "polite_constructive",
        }
    s = parsed["sentiment"]
    if "client_overall" not in s and "client" in s:
        s["client_overall"] = s.pop("client")
    if "manager_tone" not in s and "manager" in s:
        s["manager_tone"] = s.pop("manager")

    if "client_overall" in s:
        c_clean = str(s["client_overall"]).strip().lower()
        s["client_overall"] = CLIENT_SENTIMENT_MAP.get(
            c_clean,
            c_clean if c_clean in ("positive", "neutral", "negative", "conflict") else "neutral",
        )
    else:
        s["client_overall"] = "neutral"

    if "manager_tone" in s:
        m_clean = str(s["manager_tone"]).strip().lower()
        s["manager_tone"] = TONE_MAP.get(
            m_clean,
            m_clean
            if m_clean in ("polite_constructive", "indifferent", "rude", "neutral")
            else "polite_constructive",
        )
    else:
        s["manager_tone"] = "polite_constructive"

    if "client_emotion" not in s:
        emo = compute_emotion_breakdown(
            transcript,
            "",
            s.get("client_overall", "neutral"),
            s.get("manager_tone", "polite_constructive"),
        )
        s.update(emo)

    # 6. manager_checklist
    if not isinstance(parsed.get("manager_checklist"), dict):
        parsed["manager_checklist"] = {}
    cl = parsed["manager_checklist"]
    checklist_keys = [
        "greeting",
        "need_identification",
        "options_offered",
        "next_step_named",
        "contacts_taken",
    ]
    for k in checklist_keys:
        cl[k] = bool(cl.get(k, False))
    if not isinstance(cl.get("forbidden_words_found"), list):
        cl["forbidden_words_found"] = []
    cl["score"] = sum(1 for k in checklist_keys if cl.get(k) is True)
    mgr_tone = s.get("manager_tone", "polite_constructive")
    compute_compliance_and_violations(cl, mgr_tone)

    # 7. Metadata
    parsed["schema_version"] = "1.0.0"
    parsed["prompt_version"] = "v1.5-ru-types"
    if not parsed.get("summary"):
        parsed["summary"] = "Деловой разговор представителя компании и клиента."

    return parsed
