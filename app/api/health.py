from fastapi import APIRouter, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app.core.config import get_settings
from app.core.cpu import has_avx2
from app.core.logging import get_logger
from app.db.session import engine
from app.queue.redis_streams import get_queue
from app.storage.s3 import get_storage

router = APIRouter(tags=["health"])
logger = get_logger("health")

_avx2_warning_logged = False


def _warn_once_if_no_avx2() -> bool:
    """Возвращает has_avx2(); предупреждение в лог — один раз за процесс."""
    global _avx2_warning_logged
    avx2 = has_avx2()
    if not avx2 and not _avx2_warning_logged:
        _avx2_warning_logged = True
        logger.warning(
            "cpu_without_avx2_asr_will_be_slow",
            hint="для SLA голосового поиска и RTF нужен процессор с AVX2",
        )
    return avx2


@router.get("/health")
async def health_check():
    cfg = get_settings()
    avx2 = _warn_once_if_no_avx2()
    return {
        "status": "ok",
        "version": cfg.build_version,
        "profile": cfg.app_profile,
        "app_profile": cfg.app_profile,
        "llm_model": cfg.local_llm_model,
        "details": {"avx2": avx2},
    }


@router.get("/ready")
async def readiness_check(response: Response):
    deps = {
        "database": False,
        "redis": False,
        "s3": False,
        "worker_long": False,
        "worker_fast": False,
    }

    # Check database
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            deps["database"] = True
    except Exception as e:
        logger.warning("database_health_check_failed", error=str(e))

    # Check Redis
    queue = None
    try:
        queue = get_queue()
        deps["redis"] = await queue.check_health()
    except Exception as e:
        logger.warning("redis_health_check_failed", error=str(e))

    # Check S3 / MinIO
    try:
        storage = get_storage()
        deps["s3"] = storage.check_health()
    except Exception as e:
        logger.warning("s3_health_check_failed", error=str(e))

    # Workers are ready only after model warmup (Redis flags with TTL).
    if queue is not None:
        try:
            deps["worker_long"] = await queue.is_worker_ready("long")
            deps["worker_fast"] = await queue.is_worker_ready("fast")
        except Exception as e:
            logger.warning("worker_ready_check_failed", error=str(e))

    is_ready = all(deps.values())
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "unavailable",
            "dependencies": deps,
        }

    return {
        "status": "ready",
        "dependencies": deps,
    }


@router.get("/metrics")
async def get_metrics():
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
