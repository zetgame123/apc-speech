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
            "Ты — русскоязычный аналитик деловых телефонных звонков (включая рекламу, B2B продажи, IT, услуги и клиентский сервис). "
            "Проанализируй диалог менеджера и клиента и заполни JSON по схеме. "
            "Все ключи и строковые значения в JSON должны быть строго на русском языке или латинице. "
            "Ключи в entities: city, format, quantity, period, budget.\n\n"
            f"Транскрипт:\n{trimmed_transcript}"
        )
        schema = StructuredAnalysis.model_json_schema()

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{self.endpoint_url}/chat/completions",
                json={
                    "model": self.model_name,
                    "messages": [
                        {
                            "role": "system",
                            "content": "Ты — аналитик деловых звонков. Отвечай только валидным JSON согласно схеме на русском языке.",
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

        # Build prompt using the most informative turns (first 40 turns)
        turns_to_eval = segments[:40]
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
            "Определи, кто из них 'manager' (представитель/организатор/оператор компании), "
            "а кто 'client' (клиент/заказчик/собеседник). "
            "Верни JSON со списком roles (порядок реплик) и/или полями manager и client:\n"
            f"{dialogue_excerpt}"
        )

        schema = {
            "type": "object",
            "properties": {
                "manager": {
                    "type": "string",
                    "description": "Метка спикера-менеджера (например SPEAKER_0 или SPEAKER_1)",
                },
                "client": {
                    "type": "string",
                    "description": "Метка спикера-клиента (например SPEAKER_0 или SPEAKER_1)",
                },
                "roles": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["manager", "client"],
                    },
                },
            },
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
                                "name": "RoleClassification",
                                "schema": schema,
                            },
                        },
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

                if mgr_spk and cli_spk and mgr_spk in ("SPEAKER_0", "SPEAKER_1"):
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
        for city in ["Москва", "Балашиха", "Химки", "Мытищи", "Подольск", "Одинцово"]:
            if city.lower() in text_lower:
                cities.append(city)

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
            },
            manager_checklist={
                "greeting": greeting,
                "need_identification": need_id,
                "options_offered": options,
                "next_step_named": next_step,
                "contacts_taken": contacts,
                "forbidden_words_found": forbidden_found,
                "score": score,
            },
        )
