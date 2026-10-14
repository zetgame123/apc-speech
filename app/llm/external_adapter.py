import json

import httpx

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.llm.port import AnalysisContext, AnalysisModelPort, StructuredAnalysis
from app.pii.masker import PiiMasker

logger = get_logger("external_llm_adapter")


class ExternalProviderAdapter(AnalysisModelPort):
    def __init__(self):
        settings = get_settings()
        self.enabled = settings.llm_external_provider_enabled
        self.api_url = settings.llm_external_provider_url
        self.api_key = settings.llm_external_provider_api_key
        self.model = settings.llm_external_provider_model
        self.masker = PiiMasker()

    async def analyze(
        self,
        masked_transcript: str,
        context: AnalysisContext,
    ) -> StructuredAnalysis:
        if not self.enabled:
            raise AppError(
                error="external_provider_disabled",
                message="Внешний LLM провайдер отключен в конфигурации сервиса",
                status_code=403,
            )

        if not self.api_url or not self.api_key:
            raise AppError(
                error="external_provider_not_configured",
                message="Отсутствуют параметры подключения к внешнему LLM провайдеру",
                status_code=500,
            )

        # CRITICAL SECURITY ASSERT: Verify that text has NO leaked unmasked PII
        self.masker.assert_no_leaked_pii(masked_transcript)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        prompt = (
            "You are a call analyst for an outdoor advertising agency. "
            "Analyze the Russian transcript and output JSON strictly conforming to the requested schema.\n\n"
            f"{masked_transcript}"
        )

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                self.api_url,
                headers=headers,
                json={
                    "model": self.model,
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
