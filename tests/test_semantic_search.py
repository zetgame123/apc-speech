"""Tests for Stage 2: semantic & hybrid search (pgvector / SQLite fallback),
ADR-004 PII-free embedding text construction, namespace isolation,
graceful degradation without ONNX weights, and embedding reindex step.
"""

from __future__ import annotations

import math

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.analytics.embeddings import (
    EMBEDDING_DIM,
    TextEmbeddingEngine,
    build_call_embedding_text,
)
from app.db.models import Analysis, Call, Embedding, TranscriptSegment
from app.main import app
from tests.conftest import TestingSessionLocal

AUTH_HEADERS = {"Authorization": "Bearer dev-token"}


def _unit_vec(weights: dict[int, float]) -> list[float]:
    vec = [0.0] * EMBEDDING_DIM
    for idx, val in weights.items():
        vec[idx] = float(val)
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class _FakeEmbeddingEngine:
    model_name = "multilingual-e5-small-int8-test"

    def __init__(
        self,
        *,
        available: bool = True,
        query_vec: list[float] | None = None,
        passage_vec: list[float] | None = None,
    ) -> None:
        self._available = available
        self._query_vec = query_vec or _unit_vec({0: 1.0})
        self._passage_vec = passage_vec or _unit_vec({0: 1.0})

    def is_available(self) -> bool:
        return self._available

    def embed_query(self, text: str) -> list[float]:
        return list(self._query_vec)

    def embed_passage(self, text: str) -> list[float]:
        return list(self._passage_vec)

    def embed_query_safe(self, text: str) -> list[float] | None:
        return list(self._query_vec) if self._available else None

    def embed_passage_safe(self, text: str) -> list[float] | None:
        return list(self._passage_vec) if self._available else None


def test_build_call_embedding_text_excludes_original_pii():
    """ADR-004: embedding text must never include text_original or unmasked PII."""
    analysis = {
        "inquiry_type": "pricing",
        "summary": "Клиент интересовался ценой щита 3х6, звонил с +7 (999) 123-45-67.",
        "entities": {
            "cities": ["Москва"],
            "formats": ["щит 3х6"],
            "budget_rub": 150000,
            "product_category": "outdoor",
        },
        "agreements": [
            {
                "owner": "manager",
                "action": "Отправить прайс на client@example.com",
                "deadline": "завтра",
            }
        ],
    }
    segments = [
        {
            "speaker_role": "client",
            "text_original": "Секретный телефон 89165554433 и почта secret@corp.ru",
            "text_masked": "Интересует аренда щита на МКАД, телефон [ТЕЛЕФОН]",
        }
    ]

    text = build_call_embedding_text(analysis, segments)

    assert "89165554433" not in text
    assert "secret@corp.ru" not in text
    assert "999" not in text
    assert "client@example.com" not in text
    assert "Москва" in text
    assert "щит 3х6" in text
    assert "Интересует аренда щита на МКАД" in text


async def _seed_call_with_embedding(
    *,
    call_id: str,
    namespace: str,
    text_masked: str,
    summary: str,
    vector: list[float] | None,
    manager_id: str | None = None,
    inquiry_type: str = "pricing",
) -> None:
    async with TestingSessionLocal() as db:
        db.add(
            Call(
                id=call_id,
                namespace=namespace,
                external_id=f"ext-{call_id}",
                manager_id=manager_id,
                duration_sec=60.0,
                is_answering_machine=False,
            )
        )
        db.add(
            TranscriptSegment(
                call_id=call_id,
                namespace=namespace,
                segment_order=1,
                start_time=0.0,
                end_time=10.0,
                speaker_label="SPEAKER_00",
                speaker_role="client",
                text_original=text_masked,
                text_masked=text_masked,
            )
        )
        db.add(
            Analysis(
                call_id=call_id,
                namespace=namespace,
                schema_version="1.0.0",
                prompt_version="v1.5-ru-types",
                summary=summary,
                call_result="info_provided",
                inquiry_type=inquiry_type,
                entities={"cities": ["Москва"], "formats": ["3х6"], "product_category": "outdoor"},
                agreements=[],
                sentiment={"overall": "neutral"},
                manager_checklist={"score": 3},
                speech_metrics={},
            )
        )
        if vector is not None:
            db.add(
                Embedding(
                    call_id=call_id,
                    namespace=namespace,
                    vector_json=vector,
                    embedding=vector,
                    model_name="multilingual-e5-small-int8-test",
                )
            )
        await db.commit()


@pytest.mark.asyncio
async def test_semantic_search_ranking_min_score_and_namespace_isolation(monkeypatch):
    ns_a = "sem_ns_a"
    ns_b = "sem_ns_b"

    # Query vector points along axis 0
    q_vec = _unit_vec({0: 1.0})
    monkeypatch.setattr(
        "app.api.calls.get_embedding_engine",
        lambda: _FakeEmbeddingEngine(available=True, query_vec=q_vec),
    )

    # High similarity (~0.98)
    await _seed_call_with_embedding(
        call_id="call-high",
        namespace=ns_a,
        text_masked="Обсуждение прайса на билборды",
        summary="Высокая релевантность по прайсу",
        vector=_unit_vec({0: 0.98, 1: 0.2}),
        manager_id="mgr-1",
        inquiry_type="pricing",
    )
    # Medium similarity (~0.707)
    await _seed_call_with_embedding(
        call_id="call-med",
        namespace=ns_a,
        text_masked="Вопрос по срокам монтажа баннера",
        summary="Средняя релевантность по монтажу",
        vector=_unit_vec({0: 0.707, 1: 0.707}),
        manager_id="mgr-2",
        inquiry_type="production",
    )
    # Low similarity (0.0) — below min_score=0.5
    await _seed_call_with_embedding(
        call_id="call-low",
        namespace=ns_a,
        text_masked="Нецелевой звонок про доставку пиццы",
        summary="Низкая релевантность",
        vector=_unit_vec({2: 1.0}),
        manager_id="mgr-1",
        inquiry_type="spam_irrelevant",
    )
    # Perfect match in ANOTHER namespace — must be strictly isolated
    await _seed_call_with_embedding(
        call_id="call-other-ns",
        namespace=ns_b,
        text_masked="Идеальный звонок из чужого namespace",
        summary="Чужой namespace",
        vector=_unit_vec({0: 1.0}),
        manager_id="mgr-1",
        inquiry_type="pricing",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/v1/calls",
            params={
                "namespace": ns_a,
                "q": "сколько стоит аренда рекламного щита",
                "search_mode": "semantic",
                "min_score": 0.5,
            },
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["search_mode"] == "semantic"
        ids = [item["call_id"] for item in body["items"]]
        assert ids == ["call-high", "call-med"]
        assert body["items"][0]["relevance_score"] > body["items"][1]["relevance_score"]
        assert body["items"][0]["summary"] == "Высокая релевантность по прайсу"

        # Filter by manager_id must apply in semantic mode too
        resp_mgr = await client.get(
            "/v1/calls",
            params={
                "namespace": ns_a,
                "q": "стоимость",
                "search_mode": "semantic",
                "min_score": 0.5,
                "manager_id": "mgr-2",
            },
            headers=AUTH_HEADERS,
        )
        assert resp_mgr.status_code == 200
        assert [item["call_id"] for item in resp_mgr.json()["items"]] == ["call-med"]


@pytest.mark.asyncio
async def test_hybrid_search_rrf_combines_keyword_and_semantic(monkeypatch):
    ns = "hybrid_ns"
    q_vec = _unit_vec({0: 1.0})
    monkeypatch.setattr(
        "app.api.calls.get_embedding_engine",
        lambda: _FakeEmbeddingEngine(available=True, query_vec=q_vec),
    )

    # Matches BOTH keyword ("билборд") and semantic (axis 0)
    await _seed_call_with_embedding(
        call_id="call-both",
        namespace=ns,
        text_masked="Хотим арендовать билборд на Ленинградке",
        summary="Совпадает и по тексту, и по смыслу",
        vector=_unit_vec({0: 0.95, 1: 0.31}),
    )
    # Matches semantic only (synonym "рекламный щит", no word "билборд")
    await _seed_call_with_embedding(
        call_id="call-sem-only",
        namespace=ns,
        text_masked="Нужен рекламный щит на трассе",
        summary="Совпадает только по смыслу",
        vector=_unit_vec({0: 0.99, 1: 0.14}),
    )
    # Matches keyword only (orthogonal vector)
    await _seed_call_with_embedding(
        call_id="call-kw-only",
        namespace=ns,
        text_masked="Старый билборд сломался после шторма",
        summary="Совпадает только по ключевому слову",
        vector=_unit_vec({5: 1.0}),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/v1/calls",
            params={
                "namespace": ns,
                "q": "билборд",
                "search_mode": "hybrid",
                "min_score": 0.5,
            },
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        ids = [item["call_id"] for item in body["items"]]
        # call-both receives RRF boost from both keyword and semantic rankings -> #1
        assert ids[0] == "call-both"
        assert set(ids) == {"call-both", "call-sem-only", "call-kw-only"}
        assert all(item["relevance_score"] is not None for item in body["items"])


@pytest.mark.asyncio
async def test_graceful_degradation_when_embedding_model_missing(monkeypatch, tmp_path):
    ns = "degraded_ns"
    missing_engine = TextEmbeddingEngine(model_dir=str(tmp_path / "nonexistent"), enabled=True)
    assert not missing_engine.is_available()
    monkeypatch.setattr("app.api.calls.get_embedding_engine", lambda: missing_engine)
    monkeypatch.setattr("app.services.reprocess.get_embedding_engine", lambda: missing_engine)

    await _seed_call_with_embedding(
        call_id="call-deg",
        namespace=ns,
        text_masked="Клиент спрашивает про суперсайт в Казани",
        summary="Запрос по суперсайту",
        vector=None,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Semantic mode degrades gracefully with empty list and degraded=True
        resp_sem = await client.get(
            "/v1/calls",
            params={"namespace": ns, "q": "суперсайт", "search_mode": "semantic"},
            headers=AUTH_HEADERS,
        )
        assert resp_sem.status_code == 200
        assert resp_sem.json()["items"] == []
        assert resp_sem.json()["degraded"] is True

        # Hybrid mode degrades gracefully to keyword results
        resp_hyb = await client.get(
            "/v1/calls",
            params={"namespace": ns, "q": "суперсайт", "search_mode": "hybrid"},
            headers=AUTH_HEADERS,
        )
        assert resp_hyb.status_code == 200
        hyb_body = resp_hyb.json()
        assert hyb_body["degraded"] is True
        assert [i["call_id"] for i in hyb_body["items"]] == ["call-deg"]

        # Reprocess embedding step also degrades gracefully without raising 500
        resp_rep = await client.post(
            "/v1/calls/call-deg/reprocess",
            params={"namespace": ns},
            json={"steps": ["embedding"], "async_mode": False},
            headers=AUTH_HEADERS,
        )
        assert resp_rep.status_code == 200
        assert resp_rep.json()["embedding_updated"] is False


@pytest.mark.asyncio
async def test_reprocess_embedding_single_and_namespace_batch(monkeypatch):
    ns = "reindex_ns"
    expected_vec = _unit_vec({3: 1.0})
    monkeypatch.setattr(
        "app.services.reprocess.get_embedding_engine",
        lambda: _FakeEmbeddingEngine(available=True, passage_vec=expected_vec),
    )

    await _seed_call_with_embedding(
        call_id="call-idx-1",
        namespace=ns,
        text_masked="Нужен цифровой экран в центре Москвы",
        summary="Запрос на цифровой экран",
        vector=None,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Single call embedding reprocess
        resp_single = await client.post(
            "/v1/calls/call-idx-1/reprocess",
            params={"namespace": ns},
            json={"steps": ["embedding"], "async_mode": False},
            headers=AUTH_HEADERS,
        )
        assert resp_single.status_code == 200
        assert resp_single.json()["embedding_updated"] is True

        # Batch namespace reprocess with pii + embedding
        resp_batch = await client.post(
            "/v1/admin/reprocess-namespace",
            json={"namespace": ns, "steps": ["pii", "embedding"]},
            headers=AUTH_HEADERS,
        )
        assert resp_batch.status_code == 200
        batch_body = resp_batch.json()
        assert batch_body["processed_calls"] == 1
        assert batch_body["reindexed_embeddings"] == 1

    async with TestingSessionLocal() as db:
        emb = (
            await db.execute(select(Embedding).where(Embedding.call_id == "call-idx-1"))
        ).scalar_one_or_none()
        assert emb is not None
        assert emb.namespace == ns
        assert emb.model_name == "multilingual-e5-small-int8-test"
        assert len(emb.vector_json) == EMBEDDING_DIM
        assert emb.vector_json[3] == pytest.approx(1.0, abs=1e-5)
