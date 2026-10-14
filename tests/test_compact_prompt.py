"""Регрессия компактного промпта v1.3: экономия токенов не должна душить сущности."""

import inspect

from app.llm.local_adapter import LocalLlamaCppAdapter


def test_compact_prompt_preserves_entities_clause():
    src = inspect.getsource(LocalLlamaCppAdapter._analyze_via_llm)
    assert "извлекай ПОЛНОСТЬЮ" in src
    assert "пустые списки не расписывай" not in src
