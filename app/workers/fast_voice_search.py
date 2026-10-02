import asyncio

from app.asr.engine import get_voice_search_engine
from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging

settings = get_settings()
setup_logging(settings.log_level)
logger = get_logger("fast_voice_search_worker")


def prewarm():
    logger.info("prewarming_voice_search_engine", model=settings.voice_search_model_name)
    engine = get_voice_search_engine()
    engine.load_model()
    logger.info("voice_search_engine_prewarmed_ready")


async def main():
    prewarm()
    # Keep container alive if running as separate service
    stop_event = asyncio.Event()
    try:
        await stop_event.wait()
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        logger.info("fast_voice_search_worker_stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
