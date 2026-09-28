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
        prompt = (
            "Ты — аналитик звонков агентства наружной рекламы. "
            "Проанализируй диалог менеджера и клиента и заполни JSON строго по схеме.\n\n"
            f"Транскрипт:\n{masked_transcript}"
        )

        async with httpx.AsyncClient(timeout=35.0) as client:
            resp = await client.post(
                f"{self.endpoint_url}/chat/completions",
                json={
                    "model": self.model_name,
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.1,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            return StructuredAnalysis(**parsed)

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
        budget_match = re.search(r'бюджет.*?(\d[\d\s]*000)\s*руб', text_lower)
        if not budget_match:
            budget_match = re.search(r'до\s+(\d[\d\s]*000)\s*руб', text_lower)
        if budget_match:
            try:
                budget_rub = int(re.sub(r'\s+', '', budget_match.group(1)))
            except ValueError:
                pass

        quantity = None
        qty_match = re.search(r'(\d+)\s*(щит|сторон|поверхност|экран)', text_lower)
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

        # 3. Manager checklist evaluation
        checklist_items = self.checklist_cfg.get("items", {})
        score = 0
        greeting = any(p in text_lower for p in checklist_items.get("greeting", {}).get("patterns", []))
        need_id = any(p in text_lower for p in checklist_items.get("need_identification", {}).get("patterns", []))
        options = any(p in text_lower for p in checklist_items.get("options_offered", {}).get("patterns", []))
        next_step = any(p in text_lower for p in checklist_items.get("next_step_named", {}).get("patterns", []))
        contacts = any(p in text_lower for p in checklist_items.get("contacts_taken", {}).get("patterns", []))

        forbidden_found = [
            fw for fw in self.checklist_cfg.get("forbidden_words", []) if fw in text_lower
        ]

        score = sum([greeting, need_id, options, next_step, contacts])

        # 4. Agreements
        agreements = []
        if next_step:
            agreements.append({
                "who": "manager",
                "what": "Подготовить расчет и направить коммерческое предложение",
                "deadline": "В течение рабочего дня",
            })

        summary = f"Обсуждение размещения наружной рекламы по тематике '{detected_type}'."
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
