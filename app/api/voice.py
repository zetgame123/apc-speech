import asyncio
import io
import json
import os
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Header, Query, Request, Response, UploadFile
from pydantic import BaseModel

from app.api.rate_limit import rate_limit
from app.asr.engine import get_voice_search_engine
from app.asr.normalizer import RussianTextNormalizer
from app.asr.preprocessor import AudioPreprocessor
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.security import AuthContext, verify_auth
from app.pii.masker import PiiMasker

router = APIRouter(prefix="/v1/voice", tags=["voice"])
logger = get_logger("voice_search_api")

_voice_search_semaphore = asyncio.Semaphore(4)

VOICE_SEARCH_INITIAL_PROMPT = (
    "Поиск и аренда наружной рекламы в Москве и Московской области: "
    "свободные рекламные щиты 3×6, ситиборды, сити-формат, билборды, суперсайты 5×12, медиафасады, digital-экраны. "
    "Периоды размещения: на октябрь, на ноябрь, на декабрь, на январь, на весну, на месяц, на полгода. "
    "Локации и районы: Бутово, Южное Бутово, Северное Бутово, Митино, Строгино, Марьино, Чертаново, Люблино, "
    "Балашиха, Химки, Мытищи, Подольск, Красногорск, Одинцово, Люберцы, Королёв, МКАД, ТТК, шоссе, проспект, метро."
)
VOICE_SEARCH_HOTWORDS = (
    "ситиборд ситиборды ситиборда ситибордов билборд билборды суперсайт сити-формат медиафасад "
    "Бутово Бутове Южное Северное Митино Строгино Марьино Чертаново Люблино "
    "Балашиха Химки Мытищи Подольск Красногорск Одинцово Люберцы "
    "свободные свободны октябрь ноябрь декабрь январь февраль март апрель май июнь июль август сентябрь "
    "аренда 3×6 5×12 щит щиты реклама МКАД ТТК"
)


def is_16k_mono_pcm_wav(data: bytes) -> bool:
    if len(data) < 44:
        return False
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE" and data[12:16] == b"fmt ":
        audio_format = int.from_bytes(data[20:22], "little")
        channels = int.from_bytes(data[22:24], "little")
        sample_rate = int.from_bytes(data[24:28], "little")
        bits_per_sample = int.from_bytes(data[34:36], "little")
        return (
            audio_format == 1 and channels == 1 and sample_rate == 16000 and bits_per_sample == 16
        )
    return False


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

    max_voice_bytes = 10 * 1024 * 1024
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > max_voice_bytes:
                raise AppError(
                    "payload_too_large",
                    "Размер аудио для голосового поиска превышает 10 МБ",
                    413,
                )
        except ValueError:
            pass

    # Read audio bytes either from multipart file or streaming raw body
    if file:
        chunk = await file.read(max_voice_bytes + 1)
        if len(chunk) > max_voice_bytes:
            raise AppError(
                "payload_too_large", "Размер аудио для голосового поиска превышает 10 МБ", 413
            )
        audio_bytes = chunk
    else:
        chunks = []
        total_len = 0
        async for chunk in request.stream():
            total_len += len(chunk)
            if total_len > max_voice_bytes:
                raise AppError(
                    "payload_too_large",
                    "Размер аудио для голосового поиска превышает 10 МБ",
                    413,
                )
            chunks.append(chunk)
        audio_bytes = b"".join(chunks)

    if not audio_bytes:
        raise AppError("empty_audio", "Тело запроса не содержит аудиоданных", 400)

    # Preprocess and transcribe in background worker thread to prevent event loop blocking
    def _sync_process(raw_bytes: bytes, lang: str):
        if is_16k_mono_pcm_wav(raw_bytes):
            clean_bytes = raw_bytes
        else:
            preproc = AudioPreprocessor()
            clean_bytes = preproc.preprocess_memory(raw_bytes)
        eng = get_voice_search_engine()
        stream = io.BytesIO(clean_bytes)
        cfg = get_settings()
        beam_size = max(2, cfg.voice_search_beam_size)
        segs, metadata = eng.transcribe(
            stream,
            language=lang,
            beam_size=beam_size,
            initial_prompt=VOICE_SEARCH_INITIAL_PROMPT,
            hotwords=VOICE_SEARCH_HOTWORDS,
            vad_filter=True,
            vad_parameters=dict(
                min_silence_duration_ms=250,
                speech_pad_ms=200,
            ),
            word_timestamps=False,
        )
        return segs, metadata

    async with _voice_search_semaphore:
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

    raw_text = " ".join(s["text"] for s in segments).strip()
    full_text = RussianTextNormalizer().normalize(raw_text)
    avg_conf = sum(s.get("confidence", 0.0) for s in segments) / len(segments) if segments else 0.0

    elapsed_ms = int((time.time() - start_time) * 1000)
    logger.info(
        "voice_search_completed", duration=duration, latency_ms=elapsed_ms, text_len=len(full_text)
    )

    # Save to shared query history in Redis so all devices see queries
    try:
        from app.queue.redis_streams import get_queue

        queue = get_queue()
        client = await queue.get_client()
        masked_history_text = PiiMasker().mask_text(full_text)
        history_item = {
            "id": str(uuid.uuid4()),
            "text": masked_history_text,
            "language": meta.get("language", "ru"),
            "confidence": round(avg_conf, 3),
            "duration": round(duration, 2),
            "latency": elapsed_ms,
            "method": "🎤 Голосовой",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
        }
        await client.lpush(
            f"voice:history:{namespace}", json.dumps(history_item, ensure_ascii=False)
        )
        await client.ltrim(f"voice:history:{namespace}", 0, 99)
        ttl_seconds = max(1, getattr(settings, "retention_audio_days", 14) * 86400)
        await client.expire(f"voice:history:{namespace}", ttl_seconds)
    except Exception as err:
        logger.warning("voice_history_save_failed", error=str(err))

    return VoiceSearchResponse(
        text=full_text,
        language=meta.get("language", "ru"),
        confidence=round(avg_conf, 3),
        duration_sec=duration,
        processing_time_ms=elapsed_ms,
    )


@router.get(
    "/history",
    summary="Получение общей истории голосовых поисковых запросов",
)
async def get_voice_history(
    namespace: str = Query(..., description="Namespace изоляции данных"),
    auth: AuthContext = Depends(verify_auth),
):
    auth.require_scope("read")
    auth.verify_namespace(namespace)
    try:
        from app.queue.redis_streams import get_queue

        queue = get_queue()
        client = await queue.get_client()
        raw_items = await client.lrange(f"voice:history:{namespace}", 0, 99)
        items = []
        for raw in raw_items:
            try:
                decoded = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                items.append(json.loads(decoded))
            except Exception:
                continue
        return {"items": items, "count": len(items)}
    except Exception as err:
        logger.warning("voice_history_fetch_failed", error=str(err))
        return {"items": [], "count": 0}


@router.delete(
    "/history",
    summary="Очистка истории голосовых запросов",
)
async def clear_voice_history(
    namespace: str = Query(..., description="Namespace изоляции данных"),
    auth: AuthContext = Depends(verify_auth),
):
    auth.require_scope("write")
    auth.verify_namespace(namespace)
    try:
        from app.queue.redis_streams import get_queue

        queue = get_queue()
        client = await queue.get_client()
        await client.delete(f"voice:history:{namespace}")
        return {"status": "cleared", "namespace": namespace}
    except Exception as err:
        logger.warning("voice_history_clear_failed", error=str(err))
        return {"status": "error", "error": str(err)}


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
