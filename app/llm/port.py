from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel


class AnalysisContext(BaseModel):
    namespace: str
    call_id: str
    direction: str | None = "inbound"
    speech_duration_sec: float = 0.0


class StructuredAnalysis(BaseModel):
    schema_version: str = "1.0.0"
    prompt_version: str = "v1.2-ru-outdoor"
    summary: str
    call_result: str
    inquiry_type: str
    entities: dict[str, Any]
    agreements: list[dict[str, Any]]
    sentiment: dict[str, str]
    manager_checklist: dict[str, Any]
    deterministic_speech_metrics: dict[str, Any] | None = None


class AnalysisModelPort(ABC):
    @abstractmethod
    async def analyze(
        self,
        masked_transcript: str,
        context: AnalysisContext,
    ) -> StructuredAnalysis:
        """Analyzes masked call transcript and returns structured insights."""
