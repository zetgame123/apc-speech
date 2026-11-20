from app.core.config import get_settings
from app.llm.external_adapter import ExternalProviderAdapter
from app.llm.local_adapter import LocalLlamaCppAdapter
from app.llm.port import AnalysisModelPort


def get_llm_adapter() -> AnalysisModelPort:
    """Returns the configured LLM analysis adapter.

    External provider is disabled by default (LLM_EXTERNAL_PROVIDER_ENABLED=false)
    and receives only PII-masked transcripts.
    """
    settings = get_settings()
    if settings.llm_external_provider_enabled:
        return ExternalProviderAdapter()
    return LocalLlamaCppAdapter()
