import asyncio
import io
import os
import signal

from app.api.voice import VOICE_SEARCH_HOTWORDS, VOICE_SEARCH_INITIAL_PROMPT, is_16k_mono_pcm_wav
from app.asr.engine import get_voice_search_engine
from app.asr.preprocessor import AudioPreprocessor
from app.core.config import get_settings
from app.core.cpu import has_avx2
from app.core.logging import get_logger, setup_logging
from app.queue.redis_streams import GROUP_FAST_WORKERS, STREAM_FAST_JOBS, get_queue
from app.workers.warmup import mark_worker_ready, start_heartbeat_thread, synthetic_warmup_wav

settings = get_settings()
setup_logging(settings.log_level)
logger = get_logger("fast_voice_search_worker")
is_running = True


def handle_sigterm(*_args):
    global is_running
    is_running = False
    logger.info("fast_voice_search_worker_stopping")


signal.signal(signal.SIGTERM, handle_sigterm)
signal.signal(signal.SIGINT, handle_sigterm)


def prewarm():
    logger.info("prewarming_voice_search_engine", model=settings.voice_search_model_name)
    engine = get_voice_search_engine()
    engine.load_model()
    # Холостой прогон на синтетике: греем ядра инференса до первого запроса.
    try:
        engine.transcribe(
            io.BytesIO(synthetic_warmup_wav()),
            language="ru",
            beam_size=1,
            temperature=0.0,
            vad_filter=True,
            word_timestamps=False,
        )
        logger.info("voice_search_warmup_run_completed")
    except Exception as e:
        logger.warning("voice_search_warmup_run_failed", error=str(e))
    logger.info("voice_search_engine_prewarmed_ready")


def transcribe(payload: dict) -> tuple[list[dict], dict]:
    raw_bytes = bytes.fromhex(payload["audio_hex"])
    cfg = get_settings()
    if cfg.voice_search_engine == "tone":
        from app.asr.tone_engine import get_tone_engine

        return get_tone_engine().transcribe(raw_bytes, language=payload.get("language", "ru"))
    if is_16k_mono_pcm_wav(raw_bytes):
        clean_bytes = raw_bytes
    else:
        clean_bytes = AudioPreprocessor().preprocess_memory(raw_bytes)

    segments, metadata = get_voice_search_engine().transcribe(
        io.BytesIO(clean_bytes),
        language=payload.get("language", "ru"),
        beam_size=max(cfg.voice_search_beam_min, cfg.voice_search_beam_size),
        temperature=0.0,
        initial_prompt=(
            cfg.voice_search_initial_prompt
            if cfg.voice_search_initial_prompt is not None
            else VOICE_SEARCH_INITIAL_PROMPT
        ),
        hotwords=(
            cfg.voice_search_hotwords
            if cfg.voice_search_hotwords is not None
            else VOICE_SEARCH_HOTWORDS
        ),
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 250, "speech_pad_ms": 200},
        word_timestamps=False,
    )
    return segments, metadata


async def main():
    prewarm()
    queue = get_queue()
    await queue.ensure_groups()
    # Флаг сразу после прогрева: /ready — 200, не дожидаясь первого тика heartbeat.
    try:
        await mark_worker_ready(queue, "fast")
    except Exception as e:
        logger.warning("worker_ready_mark_failed", error=str(e))
    consumer = f"fast_worker_{os.getpid()}"
    logger.info("fast_voice_search_worker_started", group=GROUP_FAST_WORKERS)
    if not has_avx2():
        logger.warning(
            "cpu_without_avx2_asr_will_be_slow",
            hint="инференс без AVX2 на порядок медленнее, SLA/RTF недостижимы",
        )

    # Heartbeat готовности — отдельный поток: переживает блокировку
    # event loop длинным синхронным инференсом.
    start_heartbeat_thread("fast")
    while is_running:
        item = await queue.read_next_job(
            stream=STREAM_FAST_JOBS,
            group=GROUP_FAST_WORKERS,
            consumer=consumer,
            block_ms=1000,
            claim_min_idle_ms=10000,
        )
        if not item:
            continue

        msg_id, request_id, payload = item
        try:
            segments, metadata = await asyncio.to_thread(transcribe, payload)
            await queue.publish_fast_result(
                request_id,
                {"segments": segments, "metadata": metadata},
            )
        except Exception as exc:
            logger.error("fast_voice_search_failed", request_id=request_id, error=str(exc))
            await queue.publish_fast_result(
                request_id,
                {"error": "transcription_failed"},
            )
        finally:
            await queue.ack_job(STREAM_FAST_JOBS, GROUP_FAST_WORKERS, msg_id)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
