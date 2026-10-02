from fastapi import APIRouter, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import engine
from app.queue.redis_streams import get_queue
from app.storage.s3 import get_storage

router = APIRouter(tags=["health"])
logger = get_logger("health")


@router.get("/health")
async def health_check():
    return {
        "status": "ok",
        "version": "1.0.0",
        "profile": get_settings().app_profile,
    }


@router.get("/ready")
async def readiness_check(response: Response):
    deps = {
        "database": False,
        "redis": False,
        "s3": False,
    }

    # Check database
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            deps["database"] = True
    except Exception as e:
        logger.warning("database_health_check_failed", error=str(e))

    # Check Redis
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
