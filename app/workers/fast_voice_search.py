import asyncio

from app.asr.engine import get_asr_engine
from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging

settings = get_settings()
setup_logging(settings.log_level)
logger = get_logger("fast_voice_search_worker")


def prewarm():
    logger.info("prewarming_voice_search_engine", model=settings.voice_search_model_name)
    engine = get_asr_engine()
    engine.load_model()
    logger.info("voice_search_engine_prewarmed_ready")


if __name__ == "__main__":
    prewarm()
    # Keep container alive if running as separate service
    try:
        asyncio.get_event_loop().run_forever()
    except (KeyboardInterrupt, SystemExit):
        logger.info("fast_voice_search_worker_stopped")
