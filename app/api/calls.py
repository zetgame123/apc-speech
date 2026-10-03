from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import and_, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.security import AuthContext, verify_auth
from app.db.models import Analysis, AudioObject, Call, TranscriptSegment
from app.db.session import get_db
from app.pii.masker import PiiMasker
from app.storage.s3 import get_storage

router = APIRouter(prefix="/v1/calls", tags=["calls"])
_pii_masker = PiiMasker()


def _sanitize_words(words: list[dict] | None) -> list[dict] | None:
    if not words:
        return words
    sanitized = []
    for w in words:
        w_copy = dict(w)
        raw_word = str(w_copy.get("word", ""))
        masked_word = _pii_masker.mask_text(raw_word)
        if masked_word != raw_word:
            w_copy["word"] = masked_word
        else:
            digits = "".join(c for c in raw_word if c.isdigit())
            if len(digits) >= 3:
                w_copy["word"] = "[***]"
        sanitized.append(w_copy)
    return sanitized


def format_srt_timestamp(seconds: float) -> str:
    millis = int((seconds - int(seconds)) * 1000)
    mins, secs = divmod(int(seconds), 60)
    hours, mins = divmod(mins, 60)
    return f"{hours:02d}:{mins:02d}:{secs:02d},{millis:03d}"


def format_vtt_timestamp(seconds: float) -> str:
    millis = int((seconds - int(seconds)) * 1000)
    mins, secs = divmod(int(seconds), 60)
    hours, mins = divmod(mins, 60)
    return f"{hours:02d}:{mins:02d}:{secs:02d}.{millis:03d}"


@router.get("/{call_id}/transcript")
async def get_transcript(
    call_id: str,
    namespace: str = Query(...),
    format: str = Query("json", enum=["json", "text", "srt", "vtt"]),
    masked: bool = Query(True),
    auth: AuthContext = Depends(verify_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require_scope("read")
    auth.verify_namespace(namespace)

    # Verify call exists
    call_stmt = select(Call).where(Call.id == call_id, Call.namespace == namespace)
    call = (await db.execute(call_stmt)).scalar_one_or_none()
    if not call:
        raise AppError("call_not_found", f"Звонок {call_id} не найден", 404)

    # Fetch segments
    seg_stmt = (
        select(TranscriptSegment)
        .where(TranscriptSegment.call_id == call_id)
        .order_by(TranscriptSegment.segment_order)
    )
    segments = (await db.execute(seg_stmt)).scalars().all()

    if format == "text":
        lines = []
        for s in segments:
            txt = s.text_masked if masked else s.text_original
            lines.append(f"[{s.speaker_role}] {txt}")
        headers = {"Content-Disposition": f'attachment; filename="transcript_{call_id}.txt"'}
        return Response(
            content="\n".join(lines), media_type="text/plain; charset=utf-8", headers=headers
        )

    elif format == "srt":
        srt_entries = []
        for i, s in enumerate(segments, 1):
            txt = s.text_masked if masked else s.text_original
            start = format_srt_timestamp(s.start_time)
            end = format_srt_timestamp(s.end_time)
            srt_entries.append(f"{i}\n{start} --> {end}\n[{s.speaker_role}] {txt}\n")
        headers = {"Content-Disposition": f'attachment; filename="transcript_{call_id}.srt"'}
        return Response(
            content="\n".join(srt_entries), media_type="text/plain; charset=utf-8", headers=headers
        )

    elif format == "vtt":
        vtt_entries = ["WEBVTT\n"]
        for s in segments:
            txt = s.text_masked if masked else s.text_original
            start = format_vtt_timestamp(s.start_time)
            end = format_vtt_timestamp(s.end_time)
            vtt_entries.append(f"{start} --> {end}\n<v {s.speaker_role}>{txt}\n")
        headers = {"Content-Disposition": f'attachment; filename="transcript_{call_id}.vtt"'}
        return Response(
            content="\n".join(vtt_entries), media_type="text/vtt; charset=utf-8", headers=headers
        )

    # Default: JSON
    full_text = " ".join(s.text_masked if masked else s.text_original for s in segments)
    return {
        "call_id": call_id,
        "full_text": full_text,
        "segments": [
            {
                "start": s.start_time,
                "end": s.end_time,
                "speaker_label": s.speaker_label,
                "speaker_role": s.speaker_role,
                "text": s.text_masked if masked else s.text_original,
                "confidence": s.confidence,
                "words": _sanitize_words(s.words_json) if masked else s.words_json,
            }
            for s in segments
        ],
    }


@router.get("/{call_id}/analysis")
async def get_analysis(
    call_id: str,
    namespace: str = Query(...),
    auth: AuthContext = Depends(verify_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require_scope("read")
    auth.verify_namespace(namespace)

    stmt = select(Analysis).where(Analysis.call_id == call_id, Analysis.namespace == namespace)
    analysis = (await db.execute(stmt)).scalar_one_or_none()

    if not analysis:
        raise AppError(
            "analysis_not_found", f"Анализ для звонка {call_id} еще не готов или не найден", 404
        )

    return {
        "call_id": call_id,
        "schema_version": analysis.schema_version,
        "prompt_version": analysis.prompt_version,
        "summary": analysis.summary,
        "call_result": analysis.call_result,
        "inquiry_type": analysis.inquiry_type,
        "entities": analysis.entities,
        "agreements": analysis.agreements,
        "sentiment": analysis.sentiment,
        "manager_checklist": analysis.manager_checklist,
        "deterministic_speech_metrics": analysis.speech_metrics,
    }


@router.get("/{call_id}/audio")
async def get_call_audio(
    call_id: str,
    namespace: str = Query(...),
    presigned: bool = Query(
        False, description="Возвращать временный presigned URL вместо прямого потока байт"
    ),
    expires_in: int = Query(
        3600, ge=60, le=86400, description="Срок жизни presigned URL в секундах"
    ),
    auth: AuthContext = Depends(verify_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require_scope("read")
    auth.verify_namespace(namespace)

    stmt = select(AudioObject).where(
        AudioObject.call_id == call_id, AudioObject.namespace == namespace
    )
    audio_obj = (await db.execute(stmt)).scalars().first()
    if not audio_obj:
        raise AppError("audio_not_found", f"Аудио для звонка {call_id} не найдено", 404)

    storage = get_storage()

    if presigned:
        url = storage.generate_presigned_url(
            audio_obj.s3_bucket,
            audio_obj.s3_key,
            expires_in_sec=expires_in,
        )
        return {
            "call_id": call_id,
            "url": url,
            "expires_in_sec": expires_in,
            "mime_type": audio_obj.mime_type or "audio/wav",
        }

    try:
        data = storage.download_bytes(audio_obj.s3_bucket, audio_obj.s3_key)
    except Exception as e:
        raise AppError("audio_fetch_failed", f"Не удалось получить аудиофайл: {str(e)}", 500)

    media_type = audio_obj.mime_type or "audio/wav"
    return Response(content=data, media_type=media_type)


@router.get("")
async def list_calls(
    namespace: str = Query(...),
    q: str | None = Query(None, description="Полнотекстовый поиск"),
    manager_id: str | None = Query(None),
    inquiry_type: str | None = Query(None),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    cursor: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    auth: AuthContext = Depends(verify_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require_scope("read")
    auth.verify_namespace(namespace)

    query = select(Call).where(Call.namespace == namespace)

    if manager_id:
        query = query.where(Call.manager_id == manager_id)
    if date_from:
        query = query.where(Call.created_at >= date_from)
    if date_to:
        query = query.where(Call.created_at <= date_to)

    # Fulltext search: find calls whose transcript segments contain the query string
    if q:
        matching_call_ids = (
            select(TranscriptSegment.call_id)
            .where(TranscriptSegment.text_masked.ilike(f"%{q}%"))
            .distinct()
        )
        query = query.where(Call.id.in_(matching_call_ids))

    # Filter by inquiry type via Analysis table
    if inquiry_type:
        calls_with_type = select(Analysis.call_id).where(Analysis.inquiry_type == inquiry_type)
        query = query.where(Call.id.in_(calls_with_type))

    if cursor:
        try:
            if "|" in cursor:
                cur_dt_str, cur_id = cursor.split("|", 1)
                cur_dt = datetime.fromisoformat(cur_dt_str)
                # Strip tzinfo if comparing with naive datetime in DB
                if cur_dt.tzinfo is not None:
                    cur_dt = cur_dt.replace(tzinfo=None)
                query = query.where(
                    or_(
                        Call.created_at < cur_dt,
                        and_(Call.created_at == cur_dt, Call.id < cur_id),
                    )
                )
            else:
                query = query.where(Call.id < cursor)
        except Exception:
            query = query.where(Call.id < cursor)

    query = query.order_by(desc(Call.created_at), desc(Call.id)).limit(limit + 1)
    results = (await db.execute(query)).scalars().all()

    items = []
    has_next = len(results) > limit
    output_rows = results[:limit]

    for c in output_rows:
        created_str = (
            c.created_at.replace(tzinfo=timezone.utc).isoformat() if c.created_at else None
        )
        items.append(
            {
                "call_id": c.id,
                "external_id": c.external_id,
                "created_at": created_str,
                "manager_id": c.manager_id,
                "duration_sec": c.duration_sec,
                "is_answering_machine": c.is_answering_machine,
            }
        )

    if has_next and output_rows:
        last = output_rows[-1]
        dt_str = last.created_at.isoformat() if last.created_at else ""
        next_cursor = f"{dt_str}|{last.id}"
    else:
        next_cursor = None

    return {
        "items": items,
        "next_cursor": next_cursor,
    }
