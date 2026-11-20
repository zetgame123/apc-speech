import json
import os
import re
from typing import Any

import httpx
import yaml

from app.asr.normalizer import get_normalizer
from app.core.config import get_settings
from app.core.logging import get_logger
from app.llm.compliance import (
    compute_compliance_and_violations,
    compute_emotion_breakdown,
    normalize_analysis_dict,
)
from app.llm.port import AnalysisContext, AnalysisModelPort, StructuredAnalysis
from app.llm.rule_extractors import (
    PRODUCT_CATEGORY_DICT,
    backfill_entities_from_rules,
    canonical_category,
    canonical_period,
    coerce_budget,
    coerce_quantity,
    extract_budget_rub,
    extract_category,
    extract_cities,
    extract_formats,
    extract_period,
    extract_quantity,
)

logger = get_logger("local_llm_adapter")

__all__ = [
    "LocalLlamaCppAdapter",
    "PRODUCT_CATEGORY_DICT",
    "backfill_entities_from_rules",
    "canonical_category",
    "canonical_period",
    "coerce_budget",
    "coerce_quantity",
    "compute_compliance_and_violations",
    "compute_emotion_breakdown",
    "extract_budget_rub",
    "extract_category",
    "extract_cities",
    "extract_formats",
    "extract_period",
    "extract_quantity",
    "normalize_analysis_dict",
]


_THINK_TAG_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def _strip_think_tags(content: str) -> str:
    return _THINK_TAG_RE.sub("", content).strip()


def _llm_extra_kwargs(model_name: str) -> dict[str, Any]:
    """Disables Qwen3 chain-of-thought thinking mode for fast, strict JSON extraction."""
    if "qwen3" in (model_name or "").lower():
        return {"chat_template_kwargs": {"enable_thinking": False}}
    return {}


def _response_format(schema: dict) -> dict:
    """Шаг 3 robustness Qwen: режим constrained decoding из LOCAL_LLM_JSON_MODE.

    Дефолт json_schema (текущий факт); json_object — только для замеров
    накладных расходов. Неизвестное значение -> json_schema.
    """
    from app.core.config import get_settings

    if get_settings().local_llm_json_mode == "json_object":
        return {"type": "json_object"}
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "StructuredAnalysis",
            "schema": schema,
        },
    }


class LocalLlamaCppAdapter(AnalysisModelPort):
    def __init__(self):
        settings = get_settings()
        self.endpoint_url = settings.local_llm_url
        self.model_name = settings.local_llm_model
        self.taxonomy = self._load_taxonomy()
        self.checklist_cfg = self._load_checklist()
        self.categories = self._load_categories()

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "taxonomy" and isinstance(value, dict):
            from app.llm.port import register_taxonomy_categories

            register_taxonomy_categories(value)
        super().__setattr__(name, value)

    def _load_taxonomy(self) -> dict[str, Any]:
        path = "config/taxonomy.yaml"
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                cats = yaml.safe_load(f).get("categories", {})
                from app.llm.port import register_taxonomy_categories

                register_taxonomy_categories(cats)
                return cats
        return {}

    def _load_checklist(self) -> dict[str, Any]:
        path = "config/checklist.yaml"
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        return {"items": {}, "forbidden_words": []}

    def _load_categories(self) -> dict[str, list[str]]:
        """Закрытый список категорий из конфига; нет файла — встроенный дефолт."""
        path = "config/product_categories.yaml"
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                if isinstance(data, dict) and data:
                    return {str(k): list(v or []) for k, v in data.items()}
        return dict(PRODUCT_CATEGORY_DICT)

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
            "ЭКОНОМЬ ТОКЕНЫ (компактный ответ v1.5): summary — 1-2 предложения, до 200 символов; "
            "what в agreements — коротко; НЕ заполняй deterministic_speech_metrics, client_emotion и score "
            "(их считают правила). Сущности (cities, formats и остальные поля) извлекай ПОЛНОСТЬЮ, "
            "экономия их не касается.\n"
            "Сущности entities — заполняй ВСЕ, что названо в диалоге (null — только если нет в тексте):\n"
            "- cities, formats: списки пополняй обязательно, не оставляй пустыми при упоминании;\n"
            "- quantity: целое число (штуки, поверхности, стороны) или null\n"
            "- target_period: ПО-РУССКИ (месяц «октябрь», квартал «первый квартал», срок «полгода») или null\n"
            "- budget_rub: ЧИСЛОМ в рублях («двести тысяч» → 200000, «полмиллиона» → 500000, "
            "«миллион» → 1000000) или null\n"
            "- product_category: ТОЛЬКО бизнес самого клиента (кто он: «автосалон», «кафе», "
            "«застройщик»); рекламируемый товар/формат сюда не писать; иначе null\n"
            "Частые путаницы типов (примеры):\n"
            "- «выцвел баннер, требую замену» → complaint, а не production;\n"
            "- «мы владельцы щитов, хотим сдать» → partnership_supply, а не inventory_search;\n"
            "- «свободен ли щит на октябрь» → availability, а не inventory_search;\n"
            "- «просто узнаю, что у вас есть» → other (перечень форматов менеджером — не подбор).\n"
            "ВАЖНО по полям категории, результата и ролей:\n"
            "- inquiry_type ОБЯЗАТЕЛЬНО должен быть одним из английских слагов: "
            "'pricing', 'availability', 'inventory_search', 'specs', 'order_status', "
            "'production', 'documents_billing', 'complaint', 'partnership_supply', "
            "'programmatic_digital', 'spam_irrelevant', 'other'.\n"
            "- call_result ОБЯЗАТЕЛЬНО должен быть одним из английских слагов: "
            "'success_deal', 'follow_up_scheduled', 'info_provided', 'client_declined', 'unclear_spam'.\n"
            "- who в agreements ОБЯЗАТЕЛЬНО должен быть одним из: 'manager', 'client', 'both'.\n"
            "- client_overall в sentiment ОБЯЗАТЕЛЬНО должен быть одним из: 'positive', 'neutral', 'negative', 'conflict'.\n"
            "- manager_tone в sentiment ОБЯЗАТЕЛЬНО должен быть одним из: 'polite_constructive', 'indifferent', 'rude', 'neutral'.\n"
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

        # Шаг 2 robustness Qwen: один повтор при невалидном ответе.
        # В лог — только тип ошибки и номер попытки, без текста звонка и
        # значений полей (там могут быть ПД).
        last_error: Exception | None = None
        for attempt in (1, 2):
            try:
                async with httpx.AsyncClient(timeout=180.0) as client:
                    resp = await client.post(
                        f"{self.endpoint_url}/chat/completions",
                        json={
                            "model": self.model_name,
                            "messages": [
                                {
                                    "role": "system",
                                    "content": (
                                        "Ты — аналитик деловых звонков. Отвечай только валидным JSON согласно схеме. "
                                        "Поля inquiry_type, call_result, who, client_overall и manager_tone заполняй только указанными английскими слагами."
                                    ),
                                },
                                {"role": "user", "content": prompt},
                            ],
                            "response_format": _response_format(schema),
                            # Детерминированный разбор: temperature 0 +
                            # фиксированный seed (повтор B 3 раза — разброс 0).
                            "temperature": 0.0,
                            "seed": 42,
                            **_llm_extra_kwargs(self.model_name),
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    content = _strip_think_tags(data["choices"][0]["message"]["content"])
                    parsed = json.loads(content)

                    parsed = normalize_analysis_dict(parsed, trimmed_transcript)
                    # П.4: пустые поля сущностей добить правилами-экстракторами,
                    # категорию — строго по закрытому списку из конфига.
                    parsed["entities"] = backfill_entities_from_rules(
                        parsed.get("entities") or {}, trimmed_transcript, self.categories
                    )
                    return StructuredAnalysis(**parsed)
            except Exception as e:
                last_error = e
                logger.warning(
                    "local_llm_attempt_failed",
                    attempt=attempt,
                    error=type(e).__name__,
                )
                continue
        assert last_error is not None
        raise last_error

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

                elif (
                    "manager" in parsed
                    or "client" in parsed
                    or (mgr_spk is None and cli_spk is None)
                ):
                    # Monologue / single-speaker detection from LLM:
                    # If both are null/None, or if both map to the same speaker, or only one speaker was identified
                    detected_role = "manager" if (mgr_spk and not cli_spk) else "client"
                    logger.info(
                        "llm_role_refinement_monologue_detected_collapsing",
                        mgr=mgr_spk,
                        cli=cli_spk,
                        assigned_role=detected_role,
                    )
                    updated = []
                    for s in segments:
                        s_copy = dict(s)
                        s_copy["speaker_label"] = "SPEAKER_0"
                        s_copy["speaker_role"] = detected_role
                        updated.append(s_copy)
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
        normalizer = get_normalizer()
        text_norm = normalizer.normalize(text)
        text_lower = text_norm.lower()

        budget_rub = extract_budget_rub(text_lower)

        quantity = extract_quantity(text_lower)

        target_period = extract_period(text_lower)
        product_category = extract_category(text_lower, self.categories)

        # 1. Determine inquiry type
        detected_type = "other"
        max_matches = 0
        for category, info in self.taxonomy.items():
            matches = sum(1 for kw in info.get("keywords", []) if kw in text_lower)
            if matches > max_matches:
                max_matches = matches
                detected_type = category

        # 2. Extract entities
        cities = extract_cities(text_lower)

        formats = extract_formats(text_lower)

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
            prompt_version="v1.5-ru-types",
            summary=summary,
            call_result="follow_up_scheduled" if next_step else "info_provided",
            inquiry_type=detected_type,
            entities={
                "cities": cities,
                "formats": formats,
                "quantity": quantity,
                "target_period": target_period,
                "budget_rub": budget_rub,
                "product_category": product_category,
            },
            agreements=agreements,
            sentiment={
                "client_overall": "neutral",
                "manager_tone": "polite_constructive",
                **emo_data,
            },
            manager_checklist=cl_data,
        )
