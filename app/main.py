import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin, analytics, calls, dashboard, health, integrations, jobs, voice
from app.core.config import get_settings
from app.core.errors import AppError, error_response
from app.core.logging import get_logger, setup_logging
from app.db.session import init_db
from app.queue.redis_streams import get_queue
from app.storage.s3 import get_storage

settings = get_settings()
setup_logging(settings.log_level)
logger = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("service_startup_beginning", profile=settings.app_profile)

    # Security validations at startup
    if settings.app_env == "production":
        if not settings.callback_signing_secret or len(settings.callback_signing_secret) < 32:
            logger.error(
                "CRITICAL: CALLBACK_SIGNING_SECRET is missing or too short for production! "
                "Set a random string of at least 32 characters."
            )
        if "*" in settings.cors_allowed_origins:
            logger.warning(
                "CORS_ALLOWED_ORIGINS is set to '*' in production — restrict to specific domains"
            )
        if settings.dev_token:
            logger.warning("DEV_TOKEN is set in production — consider removing it")

    # 1. Initialize DB tables
    try:
        await init_db()
        logger.info("database_initialized")
    except Exception as e:
        logger.warning("database_init_failed", error=str(e))

    # 2. Ensure S3 buckets exist
    try:
        storage = get_storage()
        storage.ensure_buckets()
        logger.info("s3_buckets_initialized")
    except Exception as e:
        logger.warning("s3_buckets_init_failed", error=str(e))

    # 3. Ensure Redis Streams groups
    try:
        queue = get_queue()
        await queue.ensure_groups()
        logger.info("redis_streams_groups_initialized")
    except Exception as e:
        logger.warning("redis_groups_init_failed", error=str(e))

    # 4. Sync revoked tokens from Redis
    try:
        from app.core.security import revoke_token

        queue = get_queue()
        client = await queue.get_client()
        redis_revoked = await client.smembers("revoked_tokens")
        if redis_revoked:
            for r in redis_revoked:
                token_h = r.decode() if isinstance(r, bytes) else str(r)
                revoke_token(token_h)
            logger.info("revoked_tokens_synced", count=len(redis_revoked))
    except Exception as e:
        logger.warning("revoked_tokens_sync_failed", error=str(e))

    # 4. Warm up Voice Search ASR engine to eliminate cold-start latency
    try:
        from app.asr.engine import get_voice_search_engine

        engine = get_voice_search_engine()
        engine.load_model()
        logger.info("voice_search_engine_warmed_up")
    except Exception as e:
        logger.warning("voice_search_warmup_failed", error=str(e))

    yield

    logger.info("service_shutdown_completed")


app = FastAPI(
    title="apc-speech Service",
    version="1.0.0",
    description="Autonomous local speech recognition, diarization, and semantic analysis service",
    lifespan=lifespan,
    docs_url="/docs" if settings.debug else None,
    redoc_url=None,
)

# CORS - configurable via CORS_ALLOWED_ORIGINS env var
_origins = [o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_id_and_logging_middleware(request: Request, call_next):
    req_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
    structlog.contextvars.bind_contextvars(request_id=req_id)

    response = await call_next(request)
    response.headers["X-Request-Id"] = req_id
    return response


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    return error_response(
        error=exc.error,
        message=exc.message,
        status_code=exc.status_code,
        details=exc.details,
        headers=exc.headers,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error("unhandled_exception", error=str(exc))
    return error_response(
        error="internal_server_error",
        message="Внутренняя непредвиденная ошибка сервиса",
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        details={"error": str(exc)} if settings.debug else None,
    )


# Register API Routers
app.include_router(health.router)
app.include_router(voice.router)
app.include_router(jobs.router)
app.include_router(calls.router)
app.include_router(analytics.router)
app.include_router(admin.router)
app.include_router(dashboard.router)
app.include_router(integrations.router)
