"""Post-processing re-execution service (PII re-masking and semantic re-analysis)
without re-running ASR or diarization.
"""

import asyncio
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.embeddings import build_call_embedding_text, get_embedding_engine
from app.core.errors import AppError
from app.core.logging import get_logger
from app.db.models import Analysis, Call, Embedding, TranscriptSegment, utc_now
from app.llm.factory import get_llm_adapter
from app.llm.local_adapter import normalize_analysis_dict
from app.llm.port import AnalysisContext, validate_analysis_schema
from app.metrics.speech_metrics import SpeechMetricsCalculator
from app.pii.masker import get_pii_masker

logger = get_logger("reprocess")


def _build_answering_machine_analysis(speech_metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "prompt_version": "v1.5-ru-types",
        "summary": "Автоответчик или голосовая почта. Разговор не состоялся.",
        "call_result": "unclear_spam",
        "inquiry_type": "spam_irrelevant",
        "entities": {
            "cities": [],
            "formats": [],
            "periods": [],
            "budget_rub": None,
            "quantity": None,
            "product_category": "other",
        },
        "agreements": [],
        "sentiment": {
            "overall": "neutral",
            "client_emotion": "нейтральный",
            "manager_tone": "нейтральный",
            "pos_pct": 0.0,
            "neu_pct": 100.0,
            "neg_pct": 0.0,
        },
        "manager_checklist": {
            "greeting": False,
            "need_identification": False,
            "options_offered": False,
            "next_step_named": False,
            "contacts_taken": False,
            "score": 0,
            "compliance_score": 0,
            "violations": [],
            "recommendations": [],
        },
        "deterministic_speech_metrics": speech_metrics,
    }


def _format_masked_dialogue(segments: list[TranscriptSegment]) -> str:
    return "\n".join(f"[{seg.speaker_role}] {seg.text_masked}" for seg in segments)


def _compute_fallback_speech_metrics(
    segments: list[TranscriptSegment], duration_sec: float
) -> dict[str, Any]:
    seg_dicts = [
        {
            "start": s.start_time,
            "end": s.end_time,
            "speaker_label": s.speaker_label,
            "speaker_role": s.speaker_role,
            "text": s.text_masked,
        }
        for s in segments
    ]
    return SpeechMetricsCalculator().compute(seg_dicts, duration_sec)


async def _load_call_and_segments(
    db: AsyncSession, call_id: str, namespace: str
) -> tuple[Call, list[TranscriptSegment]]:
    call_stmt = select(Call).where(Call.id == call_id, Call.namespace == namespace)
    call = (await db.execute(call_stmt)).scalar_one_or_none()
    if not call:
        raise AppError("call_not_found", f"Звонок {call_id} не найден", 404)

    seg_stmt = (
        select(TranscriptSegment)
        .where(TranscriptSegment.call_id == call_id, TranscriptSegment.namespace == namespace)
        .order_by(TranscriptSegment.segment_order)
    )
    segments = list((await db.execute(seg_stmt)).scalars().all())
    return call, segments


async def remask_call_segments(db: AsyncSession, call_id: str, namespace: str) -> dict[str, Any]:
    """Re-runs PII masking over saved `text_original` for all segments of a call."""
    _, segments = await _load_call_and_segments(db, call_id, namespace)
    pii_masker = get_pii_masker()

    updated_segments = 0
    for seg in segments:
        new_masked = pii_masker.mask_text(seg.text_original)
        if new_masked != seg.text_masked:
            seg.text_masked = new_masked
            updated_segments += 1

    await db.commit()
    logger.info(
        "call_pii_remasked",
        call_id=call_id,
        namespace=namespace,
        total_segments=len(segments),
        updated_segments=updated_segments,
    )
    return {
        "call_id": call_id,
        "namespace": namespace,
        "total_segments": len(segments),
        "updated_segments": updated_segments,
    }


async def reanalyze_call(
    db: AsyncSession,
    call_id: str,
    namespace: str,
    *,
    remask: bool = True,
) -> dict[str, Any]:
    """Re-runs semantic analysis (and optionally PII masking) from saved segments."""
    call, segments = await _load_call_and_segments(db, call_id, namespace)
    updated_segments = 0

    if remask:
        pii_masker = get_pii_masker()
        for seg in segments:
            new_masked = pii_masker.mask_text(seg.text_original)
            if new_masked != seg.text_masked:
                seg.text_masked = new_masked
                updated_segments += 1

    full_masked_text = _format_masked_dialogue(segments)
    total_duration = float(call.duration_sec or 0.0)
    if total_duration <= 0.0 and segments:
        total_duration = max(float(s.end_time or 0.0) for s in segments)

    existing_analysis = (
        await db.execute(
            select(Analysis).where(Analysis.call_id == call_id, Analysis.namespace == namespace)
        )
    ).scalar_one_or_none()

    speech_metrics = (
        existing_analysis.speech_metrics
        if existing_analysis and existing_analysis.speech_metrics
        else _compute_fallback_speech_metrics(segments, total_duration)
    )

    if call.is_answering_machine:
        analysis_result = _build_answering_machine_analysis(speech_metrics)
    else:
        llm_adapter = get_llm_adapter()
        ctx = AnalysisContext(
            namespace=namespace,
            call_id=call_id,
            speech_duration_sec=total_duration,
        )
        structured = await llm_adapter.analyze(full_masked_text, ctx)
        analysis_result = structured.model_dump()
        analysis_result["deterministic_speech_metrics"] = speech_metrics

    try:
        analysis_result = normalize_analysis_dict(analysis_result, full_masked_text)
        validate_analysis_schema(analysis_result)
    except Exception as schema_err:
        logger.error("reprocess_schema_validation_failed", call_id=call_id, error=str(schema_err))
        raise AppError(
            error="schema_validation_failed",
            message=f"Результат анализа не соответствует схеме: {schema_err}",
            status_code=500,
        )

    await db.execute(delete(Analysis).where(Analysis.call_id == call_id))
    db.add(
        Analysis(
            call_id=call_id,
            namespace=namespace,
            schema_version=analysis_result["schema_version"],
            prompt_version=analysis_result["prompt_version"],
            summary=analysis_result["summary"],
            call_result=analysis_result["call_result"],
            inquiry_type=analysis_result["inquiry_type"],
            entities=analysis_result["entities"],
            agreements=analysis_result["agreements"],
            sentiment=analysis_result["sentiment"],
            manager_checklist=analysis_result["manager_checklist"],
            speech_metrics=speech_metrics,
        )
    )
    await db.commit()

    logger.info(
        "call_reanalyzed",
        call_id=call_id,
        namespace=namespace,
        remasked=remask,
        updated_segments=updated_segments,
        inquiry_type=analysis_result["inquiry_type"],
    )
    return {
        "call_id": call_id,
        "namespace": namespace,
        "total_segments": len(segments),
        "updated_segments": updated_segments,
        "inquiry_type": analysis_result["inquiry_type"],
        "call_result": analysis_result["call_result"],
    }


async def reindex_call_embedding(
    db: AsyncSession,
    call_id: str,
    namespace: str,
) -> dict[str, Any]:
    """Computes and upserts the semantic embedding vector for a call from masked data.

    Degrades gracefully if embeddings are disabled or local ONNX weights are missing.
    """
    _, segments = await _load_call_and_segments(db, call_id, namespace)
    analysis = (
        await db.execute(
            select(Analysis).where(Analysis.call_id == call_id, Analysis.namespace == namespace)
        )
    ).scalar_one_or_none()

    engine = get_embedding_engine()
    if not engine.is_available():
        logger.info(
            "call_embedding_skipped_unavailable",
            call_id=call_id,
            namespace=namespace,
        )
        return {
            "call_id": call_id,
            "namespace": namespace,
            "embedding_updated": False,
            "reason": "embedding_model_unavailable",
        }

    text = build_call_embedding_text(analysis, segments)
    if not text:
        return {
            "call_id": call_id,
            "namespace": namespace,
            "embedding_updated": False,
            "reason": "empty_text",
        }

    try:
        vector = await asyncio.to_thread(engine.embed_passage, text)
    except Exception as exc:
        logger.warning(
            "call_embedding_compute_failed",
            call_id=call_id,
            namespace=namespace,
            error_type=type(exc).__name__,
        )
        return {
            "call_id": call_id,
            "namespace": namespace,
            "embedding_updated": False,
            "reason": "compute_failed",
        }

    existing = (
        await db.execute(select(Embedding).where(Embedding.call_id == call_id))
    ).scalar_one_or_none()
    now = utc_now()
    if existing:
        existing.namespace = namespace
        existing.vector_json = vector
        existing.embedding = vector
        existing.model_name = engine.model_name
        existing.updated_at = now
    else:
        db.add(
            Embedding(
                call_id=call_id,
                namespace=namespace,
                vector_json=vector,
                embedding=vector,
                model_name=engine.model_name,
                created_at=now,
                updated_at=now,
            )
        )
    await db.commit()

    logger.info(
        "call_embedding_reindexed",
        call_id=call_id,
        namespace=namespace,
        model_name=engine.model_name,
        dim=len(vector),
    )
    return {
        "call_id": call_id,
        "namespace": namespace,
        "embedding_updated": True,
        "model_name": engine.model_name,
        "dim": len(vector),
    }


async def reprocess_namespace_calls(
    db: AsyncSession,
    namespace: str,
    *,
    steps: list[str],
    limit: int = 500,
) -> dict[str, Any]:
    """Batch-reprocesses calls in a namespace (e.g. after updating PII rules or embeddings)."""
    stmt = (
        select(Call.id)
        .where(Call.namespace == namespace)
        .order_by(Call.created_at.desc())
        .limit(limit)
    )
    call_ids = list((await db.execute(stmt)).scalars().all())

    do_pii = "pii" in steps
    do_analysis = "analysis" in steps
    do_embedding = "embedding" in steps

    processed_calls = 0
    total_updated_segments = 0
    reindexed_embeddings = 0
    failed_calls: list[dict[str, str]] = []

    for cid in call_ids:
        try:
            call_touched = False
            if do_analysis:
                res = await reanalyze_call(db, cid, namespace, remask=do_pii)
                total_updated_segments += int(res.get("updated_segments", 0))
                call_touched = True
            elif do_pii:
                res = await remask_call_segments(db, cid, namespace)
                total_updated_segments += int(res.get("updated_segments", 0))
                call_touched = True

            if do_embedding:
                emb_res = await reindex_call_embedding(db, cid, namespace)
                if emb_res.get("embedding_updated"):
                    reindexed_embeddings += 1
                call_touched = True

            if call_touched:
                processed_calls += 1
        except Exception as exc:
            logger.warning("namespace_reprocess_call_failed", call_id=cid, error=str(exc))
            failed_calls.append({"call_id": cid, "error": str(exc)})

    return {
        "namespace": namespace,
        "steps": steps,
        "total_calls": len(call_ids),
        "processed_calls": processed_calls,
        "updated_segments": total_updated_segments,
        "reindexed_embeddings": reindexed_embeddings,
        "failed_calls": failed_calls,
    }
