import json
import os
import re
from typing import Any

import httpx
import yaml

from app.asr.normalizer import RussianTextNormalizer
from app.core.config import get_settings
from app.core.logging import get_logger
from app.llm.port import AnalysisContext, AnalysisModelPort, StructuredAnalysis

logger = get_logger("local_llm_adapter")


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


class LocalLlamaCppAdapter(AnalysisModelPort):
    def __init__(self):
        settings = get_settings()
        self.endpoint_url = settings.local_llm_url
        self.model_name = settings.local_llm_model
        self.taxonomy = self._load_taxonomy()
        self.checklist_cfg = self._load_checklist()

    def _load_taxonomy(self) -> dict[str, Any]:
        path = "config/taxonomy.yaml"
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f).get("categories", {})
        return {}

    def _load_checklist(self) -> dict[str, Any]:
        path = "config/checklist.yaml"
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        return {"items": {}, "forbidden_words": []}

    async def analyze(
        self,
        masked_transcript: str,
        context: AnalysisContext,
    ) -> StructuredAnalysis:
        settings = get_settings()

        # If on local-full and LLM server is accessible, try LLM first
        if settings.app_profile == "local-full":
            try:
                return await self._analyze_via_llm(masked_transcript, context)
            except Exception as e:
                logger.warning("local_llm_call_failed_fallback_to_rules", error=str(e))

        # Rule-based fallback (Guaranteed to work on small profile with 0 extra memory)
        return self._analyze_via_rules(masked_transcript, context)

    async def _analyze_via_llm(
        self,
        masked_transcript: str,
        context: AnalysisContext,
    ) -> StructuredAnalysis:
        # If transcript is very long, condense it to keep prompt evaluation fast on CPU
        trimmed_transcript = masked_transcript
        if len(masked_transcript) > 4000:
            lines = masked_transcript.split("\n")
            if len(lines) > 50:
                trimmed_transcript = "\n".join(
                    lines[:30] + ["... [середина разговора] ..."] + lines[-20:]
                )

        prompt = (
            "Ты — русскоязычный аналитик деловых телефонных звонков (реклама, продажи, услуги, сервис, запись клиентов). "
            "Проанализируй диалог менеджера и клиента и заполни JSON строго по схеме.\n"
            "ВАЖНО по полям категории и результата:\n"
            "- inquiry_type ОБЯЗАТЕЛЬНО должен быть одним из английских слагов: "
            "'pricing', 'availability', 'inventory_search', 'specs', 'order_status', "
            "'production', 'documents_billing', 'complaint', 'partnership_supply', "
            "'programmatic_digital', 'spam_irrelevant', 'other'.\n"
            "- call_result ОБЯЗАТЕЛЬНО должен быть одним из английских слагов: "
            "'success_deal', 'follow_up_scheduled', 'info_provided', 'client_declined', 'unclear_spam'.\n"
            "- Текстовые поля (summary, what в agreements, сущности) заполняй на русском языке.\n\n"
            "Критерии чеклиста менеджера (manager_checklist):\n"
            "- greeting: поздоровался, представился или назвал компанию (true/false)\n"
            "- need_identification: выяснил запрос, потребность, услугу, локацию или дату (true/false)\n"
            "- options_offered: предложил конкретные варианты, услуги, время, слоты или форматы (true/false)\n"
            "- next_step_named: зафиксировал следующий шаг (запись, встречу, звонок, КП, расчет) (true/false)\n"
            "- contacts_taken: уточнил или сверил контактные данные (телефон, имя, email, мессенджер) (true/false)\n"
            "- forbidden_words_found: грубые или недопустимые слова (или [])\n"
            "- score: сумма выполненных пунктов (от 0 до 5)\n\n"
            f"Транскрипт:\n{trimmed_transcript}"
        )
        schema = StructuredAnalysis.model_json_schema()

        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(
                f"{self.endpoint_url}/chat/completions",
                json={
                    "model": self.model_name,
                    "messages": [
                        {
                            "role": "system",
                            "content": "Ты — аналитик деловых звонков. Отвечай только валидным JSON согласно схеме. Поля inquiry_type и call_result заполняй только английскими слагами.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "StructuredAnalysis",
                            "schema": schema,
                        },
                    },
                    "temperature": 0.1,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(content)

            # Map Russian synonyms to canonical slugs for inquiry_type and call_result
            inquiry_map = {
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
            }
            if "inquiry_type" in parsed:
                inq_clean = str(parsed["inquiry_type"]).strip().lower()
                if inq_clean in inquiry_map:
                    parsed["inquiry_type"] = inquiry_map[inq_clean]

            call_result_map = {
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
            if "call_result" in parsed:
                cr_clean = str(parsed["call_result"]).strip().lower()
                if cr_clean in call_result_map:
                    parsed["call_result"] = call_result_map[cr_clean]

            # Normalize sentiment and emotions
            if isinstance(parsed.get("sentiment"), dict):
                s = parsed["sentiment"]
                if "client_overall" not in s and "client" in s:
                    s["client_overall"] = s.pop("client")
                if "manager_tone" not in s and "manager" in s:
                    s["manager_tone"] = s.pop("manager")
                if "client_emotion" not in s:
                    emo = compute_emotion_breakdown(
                        trimmed_transcript,
                        "",
                        s.get("client_overall", "neutral"),
                        s.get("manager_tone", "polite_constructive"),
                    )
                    s.update(emo)

            # Guarantee manager_checklist normalization, recalculate score, compliance score and violations
            if isinstance(parsed.get("manager_checklist"), dict):
                cl = parsed["manager_checklist"]
                checklist_keys = [
                    "greeting",
                    "need_identification",
                    "options_offered",
                    "next_step_named",
                    "contacts_taken",
                ]
                for k in checklist_keys:
                    cl.setdefault(k, False)
                cl["forbidden_words_found"] = cl.get("forbidden_words_found") or []
                cl["score"] = sum(1 for k in checklist_keys if cl.get(k) is True)
                mgr_tone = (
                    parsed.get("sentiment", {}).get("manager_tone")
                    if isinstance(parsed.get("sentiment"), dict)
                    else "polite_constructive"
                )
                compute_compliance_and_violations(cl, mgr_tone)

            return StructuredAnalysis(**parsed)

    async def refine_speaker_roles(
        self,
        segments: list[dict[str, Any]],
        timeout_sec: float = 120.0,
    ) -> list[dict[str, Any]]:
        """
        Uses Qwen 2.5 7B to semantically classify/refine speaker roles ('manager' vs 'client')
        based on conversational pragmatics.
        Falls back to original segments on any error or timeout.
        """
        if not segments:
            return segments

        unique_spks = {s.get("speaker_label") for s in segments}
        if len(unique_spks) <= 1:
            return segments

        # Build prompt using the most informative turns (first 30 turns)
        turns_to_eval = segments[:30]
        lines = []
        for i, s in enumerate(turns_to_eval):
            txt = s.get("text", "").strip()
            if len(txt) > 200:
                txt = txt[:197] + "..."
            spk = s.get("speaker_label", f"speaker_{i}")
            lines.append(f"[{spk}] {txt}")

        dialogue_excerpt = "\n".join(lines)
        prompt = (
            "В телефонном разговоре участвуют собеседники (SPEAKER_0 и SPEAKER_1). "
            "Определи, кто из них 'manager' (представитель/организатор/продавец/оператор компании), "
            "а кто 'client' (клиент/заказчик/собеседник/абонент). "
            "Если это монолог, стихи, басня или не деловой диалог, укажи null. "
            "Верни JSON со спикерами manager и client:\n"
            f"{dialogue_excerpt}"
        )

        schema = {
            "type": "object",
            "properties": {
                "manager": {
                    "type": ["string", "null"],
                    "enum": ["SPEAKER_0", "SPEAKER_1", None],
                    "description": "Метка спикера-менеджера: SPEAKER_0 или SPEAKER_1 (или null)",
                },
                "client": {
                    "type": ["string", "null"],
                    "enum": ["SPEAKER_0", "SPEAKER_1", None],
                    "description": "Метка спикера-клиента: SPEAKER_0 или SPEAKER_1 (или null)",
                },
            },
            "required": ["manager", "client"],
            "additionalProperties": False,
        }

        try:
            async with httpx.AsyncClient(timeout=timeout_sec) as client:
                resp = await client.post(
                    f"{self.endpoint_url}/chat/completions",
                    json={
                        "model": self.model_name,
                        "messages": [
                            {
                                "role": "system",
                                "content": "Ты эксперт по анализу деловых телефонных разговоров. Отвечай строго валидным JSON.",
                            },
                            {"role": "user", "content": prompt},
                        ],
                        "response_format": {
                            "type": "json_schema",
                            "json_schema": {
                                "name": "SpeakerRoleMapping",
                                "schema": schema,
                            },
                        },
                        "max_tokens": 40,
                        "temperature": 0.0,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                parsed = json.loads(content)

                mgr_spk = parsed.get("manager")
                cli_spk = parsed.get("client")
                roles = parsed.get("roles", [])

                if (
                    mgr_spk
                    and cli_spk
                    and mgr_spk in ("SPEAKER_0", "SPEAKER_1")
                    and cli_spk in ("SPEAKER_0", "SPEAKER_1")
                    and mgr_spk != cli_spk
                ):
                    updated = []
                    for s in segments:
                        s_copy = dict(s)
                        spk = s_copy.get("speaker_label", "SPEAKER_0")
                        if spk == mgr_spk:
                            s_copy["speaker_role"] = "manager"
                        elif spk == cli_spk:
                            s_copy["speaker_role"] = "client"
                        updated.append(s_copy)
                    logger.info(
                        "llm_role_refinement_by_speaker_success", manager=mgr_spk, client=cli_spk
                    )
                    return updated

                elif isinstance(roles, list) and len(roles) > 0:
                    updated = []
                    for i, s in enumerate(segments):
                        s_copy = dict(s)
                        if i < len(roles) and roles[i] in ("manager", "client"):
                            s_copy["speaker_role"] = roles[i]
                            s_copy["speaker_label"] = (
                                "SPEAKER_0" if roles[i] == "manager" else "SPEAKER_1"
                            )
                        updated.append(s_copy)
                    logger.info("llm_role_refinement_by_array_success", count=len(updated))
                    return updated

        except Exception as e:
            logger.warning("llm_role_refinement_failed_fallback", error=str(e))

        return segments

    def _analyze_via_rules(
        self,
        text: str,
        context: AnalysisContext,
    ) -> StructuredAnalysis:
        """Deterministic rule-based extractor based on YAML taxonomy and checklist."""
        normalizer = RussianTextNormalizer()
        text_norm = normalizer.normalize(text)
        text_lower = text_norm.lower()

        budget_rub = None
        budget_match = re.search(r"бюджет.*?(\d[\d\s]*000)\s*руб", text_lower)
        if not budget_match:
            budget_match = re.search(r"до\s+(\d[\d\s]*000)\s*руб", text_lower)
        if budget_match:
            try:
                budget_rub = int(re.sub(r"\s+", "", budget_match.group(1)))
            except ValueError:
                pass

        quantity = None
        qty_match = re.search(r"(\d+)\s*(щит|сторон|поверхност|экран)", text_lower)
        if qty_match:
            try:
                quantity = int(qty_match.group(1))
            except ValueError:
                pass

        # 1. Determine inquiry type
        detected_type = "other"
        max_matches = 0
        for category, info in self.taxonomy.items():
            matches = sum(1 for kw in info.get("keywords", []) if kw in text_lower)
            if matches > max_matches:
                max_matches = matches
                detected_type = category

        # 2. Extract entities
        cities = []
        city_patterns = {
            "Москва": r"\bмоскв[аеыуой]?\b",
            "Балашиха": r"\bбалаших[аеуойи]?\b",
            "Химки": r"\bхимк[а-я]{1,3}\b",
            "Мытищи": r"\bмытищ[а-я]{1,3}\b",
            "Подольск": r"\bподольск[а-я]{0,3}\b",
            "Одинцово": r"\bодинцов[оае]?\b",
            "Красногорск": r"\bкрасногорск[а-я]{0,3}\b",
            "Люберцы": r"\bлюберц[а-я]{1,3}\b",
            "Королёв": r"\bкорол[её]в[а-я]{0,3}\b",
        }
        for city_name, pat in city_patterns.items():
            if re.search(pat, text_lower):
                cities.append(city_name)

        formats = []
        for fmt in ["3×6", "щит", "суперсайт", "ситиборд", "экран", "медиафасад"]:
            if fmt.lower() in text_lower:
                formats.append(fmt)

        # Extract manager lines specifically for checklist evaluation
        manager_lines = []
        client_lines = []
        for line in text.split("\n"):
            line_str = line.strip()
            if line_str.lower().startswith("[manager]"):
                manager_lines.append(line_str[len("[manager]") :].strip())
            elif line_str.lower().startswith("[client]"):
                client_lines.append(line_str[len("[client]") :].strip())

        # If manager lines exist, use them; otherwise if no speaker tags, evaluate whole text
        if manager_lines:
            manager_text = " ".join(manager_lines)
        else:
            manager_text = text if not client_lines else ""

        manager_norm = normalizer.normalize(manager_text).lower()

        # 3. Manager checklist evaluation (strictly on manager speech)
        checklist_items = self.checklist_cfg.get("items", {})
        greeting = bool(
            manager_norm
            and any(
                p in manager_norm for p in checklist_items.get("greeting", {}).get("patterns", [])
            )
        )
        need_id = bool(
            manager_norm
            and any(
                p in manager_norm
                for p in checklist_items.get("need_identification", {}).get("patterns", [])
            )
        )
        options = bool(
            manager_norm
            and any(
                p in manager_norm
                for p in checklist_items.get("options_offered", {}).get("patterns", [])
            )
        )
        next_step = bool(
            manager_norm
            and any(
                p in manager_norm
                for p in checklist_items.get("next_step_named", {}).get("patterns", [])
            )
        )
        contacts = bool(
            manager_norm
            and any(
                p in manager_norm
                for p in checklist_items.get("contacts_taken", {}).get("patterns", [])
            )
        )

        forbidden_found = [
            fw for fw in self.checklist_cfg.get("forbidden_words", []) if fw in manager_norm
        ]

        score = sum([greeting, need_id, options, next_step, contacts])

        # 4. Agreements (only if manager made explicit commitments)
        agreements = []
        commitment_keywords = [
            "пришлю",
            "направлю",
            "подготовлю",
            "отправлю",
            "перезвоню",
            "вышлю",
            "свяжусь",
            "подберу",
        ]
        if next_step and any(kw in manager_norm for kw in commitment_keywords):
            agreements.append(
                {
                    "who": "manager",
                    "what": "Подготовить расчет и направить коммерческое предложение",
                    "deadline": "В течение рабочего дня",
                }
            )

        if detected_type != "other":
            summary = f"Обсуждение коммерческого предложения по тематике '{detected_type}'."
        else:
            summary = "Деловой телефонный разговор между представителем компании и клиентом."
        if cities:
            summary += f" Локации: {', '.join(cities)}."
        if formats:
            summary += f" Форматы: {', '.join(formats)}."

        cl_data = {
            "greeting": greeting,
            "need_identification": need_id,
            "options_offered": options,
            "next_step_named": next_step,
            "contacts_taken": contacts,
            "forbidden_words_found": forbidden_found,
            "score": score,
        }
        compute_compliance_and_violations(cl_data, "polite_constructive")

        client_text = (
            " ".join(client_lines) if client_lines else (text if not manager_lines else "")
        )
        emo_data = compute_emotion_breakdown(
            client_text,
            manager_text,
            "neutral",
            "polite_constructive",
        )

        return StructuredAnalysis(
            schema_version="1.0.0",
            prompt_version="v1.2-ru-outdoor",
            summary=summary,
            call_result="follow_up_scheduled" if next_step else "info_provided",
            inquiry_type=detected_type,
            entities={
                "cities": cities,
                "formats": formats,
                "quantity": quantity,
                "target_period": None,
                "budget_rub": budget_rub,
                "product_category": None,
            },
            agreements=agreements,
            sentiment={
                "client_overall": "neutral",
                "manager_tone": "polite_constructive",
                **emo_data,
            },
            manager_checklist=cl_data,
        )
