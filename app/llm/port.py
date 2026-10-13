import json
import os
from abc import ABC, abstractmethod
from typing import Any, Literal

import jsonschema
from pydantic import BaseModel

_SCHEMA_CACHE: dict[str, Any] | None = None
_ALLOWED_TAXONOMY_CATEGORIES: set[str] = set()


def get_allowed_taxonomy_categories() -> set[str]:
    global _ALLOWED_TAXONOMY_CATEGORIES
    if not _ALLOWED_TAXONOMY_CATEGORIES:
        taxonomy_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "config",
            "taxonomy.yaml",
        )
        if os.path.exists(taxonomy_path):
            import yaml

            try:
                with open(taxonomy_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                    _ALLOWED_TAXONOMY_CATEGORIES.update(data.get("categories", {}).keys())
            except Exception:
                pass
        if not _ALLOWED_TAXONOMY_CATEGORIES:
            _ALLOWED_TAXONOMY_CATEGORIES.update(
                {
                    "pricing",
                    "availability",
                    "inventory_search",
                    "specs",
                    "order_status",
                    "production",
                    "documents_billing",
                    "complaint",
                    "partnership_supply",
                    "programmatic_digital",
                    "spam_irrelevant",
                    "other",
                }
            )
    return _ALLOWED_TAXONOMY_CATEGORIES


def register_taxonomy_categories(categories: Any) -> None:
    allowed = get_allowed_taxonomy_categories()
    if isinstance(categories, dict):
        allowed.update(categories.keys())
    elif isinstance(categories, (list, set, tuple)):
        allowed.update(categories)


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
    """Validates the analysis dictionary against schemas/analysis_schema_v1.json and configured taxonomy."""
    schema = get_analysis_json_schema()
    if schema:
        jsonschema.validate(instance=data, schema=schema)
    inquiry_type = data.get("inquiry_type")
    allowed = get_allowed_taxonomy_categories()
    if inquiry_type not in allowed:
        raise jsonschema.ValidationError(
            f"inquiry_type '{inquiry_type}' is not configured in taxonomy (allowed: {sorted(allowed)})"
        )


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
    client_overall: Literal["positive", "neutral", "negative", "conflict"] = "neutral"
    manager_tone: Literal["polite_constructive", "indifferent", "rude", "neutral"] = (
        "polite_constructive"
    )
    client_emotion: Literal[
        "enthusiasm_joy",
        "neutral_business",
        "doubt_skepticism",
        "irritation_anger",
        "urgency_anxiety",
    ] = "neutral_business"
    manager_emotion: Literal[
        "enthusiastic_helpful",
        "calm_professional",
        "indifferent_bored",
        "tense_defensive",
    ] = "calm_professional"
    emotions_breakdown: EmotionsBreakdown | dict[str, float] = EmotionsBreakdown()


class Entities(BaseModel):
    cities: list[str] = []
    formats: list[str] = []
    quantity: int | None = None
    target_period: str | None = None
    budget_rub: float | None = None
    product_category: str | None = None


class Agreement(BaseModel):
    who: Literal["manager", "client", "both"] = "manager"
    what: str
    deadline: str | None = None


class StructuredAnalysis(BaseModel):
    schema_version: str = "1.0.0"
    prompt_version: str = "v1.5-ru-types"
    summary: str
    call_result: Literal[
        "success_deal",
        "follow_up_scheduled",
        "info_provided",
        "client_declined",
        "unclear_spam",
    ]
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
