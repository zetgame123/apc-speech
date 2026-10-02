import asyncio
import io
import os
import time

from fastapi import APIRouter, Depends, File, Header, Query, Request, Response, UploadFile
from pydantic import BaseModel

from app.api.rate_limit import rate_limit
from app.asr.engine import get_voice_search_engine
from app.asr.preprocessor import AudioPreprocessor
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.security import AuthContext, verify_auth

router = APIRouter(prefix="/v1/voice", tags=["voice"])
logger = get_logger("voice_search_api")

VOICE_SEARCH_INITIAL_PROMPT = (
    "Поиск наружной рекламы: щиты 3×6, билборды, суперсайты 5×12, сити-формат, "
    "ситиборды, медиафасады, digital-экраны, аренда, Балашиха, Химки, МКАД, шоссе, метро."
)


class VoiceSearchResponse(BaseModel):
    text: str
    language: str
    confidence: float
    duration_sec: float
    processing_time_ms: int


@router.post(
    "/search",
    response_model=VoiceSearchResponse,
    summary="Синхронный голосовой поиск",
)
async def voice_search(
    request: Request,
    namespace: str = Query(..., description="Namespace изоляции данных"),
    language: str = Query("ru", description="Язык распознавания речи: ru, en или auto"),
    file: UploadFile | None = File(None),
    auth: AuthContext = Depends(verify_auth),
    _=Depends(rate_limit),
    x_request_id: str | None = Header(None),
):
    start_time = time.time()
    auth.require_scope("write")
    auth.verify_namespace(namespace)

    # Read audio bytes either from multipart file or direct raw body
    if file:
        audio_bytes = await file.read()
    else:
        audio_bytes = await request.body()

    if not audio_bytes:
        raise AppError("empty_audio", "Тело запроса не содержит аудиоданных", 400)

    # Size check (max 10MB for 60s audio)
    if len(audio_bytes) > 10 * 1024 * 1024:
        raise AppError(
            "payload_too_large", "Размер аудио для голосового поиска превышает 10 МБ", 413
        )

    # Preprocess and transcribe in background worker thread to prevent event loop blocking
    def _sync_process(raw_bytes: bytes, lang: str):
        preproc = AudioPreprocessor()
        clean_bytes = preproc.preprocess_memory(raw_bytes)
        eng = get_voice_search_engine()
        stream = io.BytesIO(clean_bytes)
        cfg = get_settings()
        segs, metadata = eng.transcribe(
            stream,
            language=lang,
            beam_size=cfg.voice_search_beam_size,
            initial_prompt=VOICE_SEARCH_INITIAL_PROMPT,
            vad_filter=True,
            word_timestamps=False,
        )
        return segs, metadata

    try:
        segments, meta = await asyncio.to_thread(_sync_process, audio_bytes, language)
    except Exception as e:
        logger.error("voice_search_transcribe_error", error=str(e))
        raise AppError("transcription_failed", "Ошибка распознавания голосового запроса", 500)

    duration = meta.get("duration", 0.0)
    settings = get_settings()
    if duration > settings.voice_search_max_duration_sec:
        raise AppError(
            "duration_exceeded",
            f"Длительность аудио ({duration} с) превышает лимит ({settings.voice_search_max_duration_sec} с)",
            400,
        )

    full_text = " ".join(s["text"] for s in segments).strip()
    avg_conf = sum(s.get("confidence", 0.0) for s in segments) / len(segments) if segments else 0.0

    elapsed_ms = int((time.time() - start_time) * 1000)
    logger.info(
        "voice_search_completed", duration=duration, latency_ms=elapsed_ms, text_len=len(full_text)
    )

    return VoiceSearchResponse(
        text=full_text,
        language=meta.get("language", "ru"),
        confidence=round(avg_conf, 3),
        duration_sec=duration,
        processing_time_ms=elapsed_ms,
    )


@router.get(
    "/sample-audio",
    summary="Получение демонстрационного аудиофайла для голосового поиска",
)
async def get_sample_voice_audio():
    static_sample = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "static",
        "sample_voice.wav",
    )
    if os.path.exists(static_sample):
        with open(static_sample, "rb") as f:
            data = f.read()
        return Response(content=data, media_type="audio/wav")

    sample_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "data",
        "evaluation_suite",
        "sample_clean_01_clean.wav",
    )
    if os.path.exists(sample_path):
        with open(sample_path, "rb") as f:
            data = f.read()
        return Response(content=data, media_type="audio/wav")
    raise AppError("sample_not_found", "Образец аудиофайла не найден", 404)
