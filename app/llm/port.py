import json
import os
from abc import ABC, abstractmethod
from typing import Any

import jsonschema
from pydantic import BaseModel

_SCHEMA_CACHE: dict[str, Any] | None = None


def get_analysis_json_schema() -> dict[str, Any]:
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is None:
        schema_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "schemas",
            "analysis_schema_v1.json",
        )
        if os.path.exists(schema_path):
            with open(schema_path, "r", encoding="utf-8") as f:
                _SCHEMA_CACHE = json.load(f)
        else:
            _SCHEMA_CACHE = {}
    return _SCHEMA_CACHE


def validate_analysis_schema(data: dict[str, Any]) -> None:
    """Validates the analysis dictionary against schemas/analysis_schema_v1.json."""
    schema = get_analysis_json_schema()
    if schema:
        jsonschema.validate(instance=data, schema=schema)


class AnalysisContext(BaseModel):
    namespace: str
    call_id: str
    direction: str | None = "inbound"
    speech_duration_sec: float = 0.0


class ManagerChecklist(BaseModel):
    greeting: bool = False
    need_identification: bool = False
    options_offered: bool = False
    next_step_named: bool = False
    contacts_taken: bool = False
    forbidden_words_found: list[str] = []
    score: int = 0
    compliance_score: int = 0
    violations: list[str] = []
    passed_rules: list[str] = []
    recommendations: list[str] = []


class EmotionsBreakdown(BaseModel):
    positive_pct: float = 0.0
    neutral_pct: float = 100.0
    negative_pct: float = 0.0


class Sentiment(BaseModel):
    client_overall: str = "neutral"
    manager_tone: str = "polite_constructive"
    client_emotion: str = "neutral_business"
    manager_emotion: str = "calm_professional"
    emotions_breakdown: EmotionsBreakdown | dict[str, float] = EmotionsBreakdown()


class Entities(BaseModel):
    cities: list[str] = []
    formats: list[str] = []
    quantity: int | None = None
    target_period: str | None = None
    budget_rub: float | None = None
    product_category: str | None = None


class Agreement(BaseModel):
    who: str = "manager"
    what: str
    deadline: str | None = None


class StructuredAnalysis(BaseModel):
    schema_version: str = "1.0.0"
    prompt_version: str = "v1.2-ru-outdoor"
    summary: str
    call_result: str
    inquiry_type: str
    entities: Entities | dict[str, Any]
    agreements: list[Agreement | dict[str, Any]] = []
    sentiment: Sentiment | dict[str, Any]
    manager_checklist: ManagerChecklist | dict[str, Any]
    deterministic_speech_metrics: dict[str, Any] | None = None


class AnalysisModelPort(ABC):
    @abstractmethod
    async def analyze(
        self,
        masked_transcript: str,
        context: AnalysisContext,
    ) -> StructuredAnalysis:
        """Analyzes masked call transcript and returns structured insights."""
