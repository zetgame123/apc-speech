from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class AnalysisContext(BaseModel):
    namespace: str
    call_id: str
    direction: Optional[str] = "inbound"
    speech_duration_sec: float = 0.0


class StructuredAnalysis(BaseModel):
    schema_version: str = "1.0.0"
    prompt_version: str = "v1.2-ru-outdoor"
    summary: str
    call_result: str
    inquiry_type: str
    entities: Dict[str, Any]
    agreements: List[Dict[str, Any]]
    sentiment: Dict[str, str]
    manager_checklist: Dict[str, Any]
    deterministic_speech_metrics: Optional[Dict[str, Any]] = None


class AnalysisModelPort(ABC):
    @abstractmethod
    async def analyze(
        self,
        masked_transcript: str,
        context: AnalysisContext,
    ) -> StructuredAnalysis:
        """Analyzes masked call transcript and returns structured insights."""
        pass
