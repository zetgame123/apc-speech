import asyncio
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

import structlog
import yaml
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import admin, analytics, calls, dashboard, health, integrations, jobs, voice
from app.core.config import get_settings
from app.core.errors import AppError, error_response
from app.core.logging import get_logger, setup_logging
from app.db.session import init_db
from app.metrics.prometheus import (
    HTTP_REQUEST_DURATION_SECONDS,
    HTTP_REQUESTS_TOTAL,
)
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
        if (
            not settings.callback_signing_secret
            or len(settings.callback_signing_secret) < 32
            or "change_me" in settings.callback_signing_secret
        ):
            msg = (
                "CRITICAL: CALLBACK_SIGNING_SECRET is missing, too short, or default placeholder for production! "
                "Set a random string of at least 32 characters."
            )
            logger.error(msg)
            raise RuntimeError(msg)
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

    # Voice-search ASR is prewarmed by the dedicated worker-fast service.
    # API processes remain lightweight and do not compete with long-audio ASR for model memory.

    # 5. Reap downloads stalled by a previous api run + start periodic sweeper.
    # (BackgroundTasks не переживают рестарт api: задание может навсегда
    # зависнуть в pending_download.)
    try:
        from app.api.audio_ingest import reap_stalled_downloads, stalled_downloads_sweeper_loop

        reaped = await reap_stalled_downloads()
        if reaped:
            logger.warning("stalled_downloads_reaped_at_startup", count=reaped)
        sweeper_task = asyncio.create_task(stalled_downloads_sweeper_loop())
    except Exception as e:
        logger.warning("stalled_downloads_sweeper_start_failed", error=str(e))
        sweeper_task = None

    yield

    if sweeper_task is not None:
        sweeper_task.cancel()
    logger.info("service_shutdown_completed")


app = FastAPI(
    title="apc-speech Service",
    version=settings.build_version,
    description="Autonomous local speech recognition, diarization, and semantic analysis service",
    lifespan=lifespan,
    docs_url="/docs" if settings.debug or settings.app_env != "production" else None,
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


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "openapi.yaml")
    if os.path.exists(openapi_path):
        with open(openapi_path, "r", encoding="utf-8") as f:
            app.openapi_schema = yaml.safe_load(f)
            return app.openapi_schema
    from fastapi.openapi.utils import get_openapi

    app.openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        openapi_version=app.openapi_version,
        description=app.description,
        routes=app.routes,
    )
    return app.openapi_schema


app.openapi = custom_openapi


def _sanitize_validation_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sanitizes Pydantic validation errors by stripping raw input/ctx to prevent PII leakage in logs."""
    sanitized = []
    for err in errors:
        item = {
            "type": err.get("type"),
            "loc": err.get("loc"),
            "msg": err.get("msg"),
        }
        sanitized.append(item)
    return sanitized


@app.middleware("http")
async def request_id_and_logging_middleware(request: Request, call_next):
    req_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
    structlog.contextvars.bind_contextvars(request_id=req_id)

    start_time = time.time()
    response = await call_next(request)
    duration = time.time() - start_time
    response.headers["X-Request-Id"] = req_id

    # Record Prometheus metrics
    endpoint = request.url.path
    HTTP_REQUESTS_TOTAL.labels(
        method=request.method, endpoint=endpoint, status=response.status_code
    ).inc()
    HTTP_REQUEST_DURATION_SECONDS.labels(method=request.method, endpoint=endpoint).observe(duration)
    logger.info(
        "request_completed",
        method=request.method,
        path=endpoint,
        status_code=response.status_code,
        duration_ms=round(duration * 1000, 2),
    )

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


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    raw_errors = exc.errors()
    sanitized = _sanitize_validation_errors(raw_errors)
    logger.warning("request_validation_failed", errors=sanitized)

    is_json_invalid = any(e.get("type") == "json_invalid" for e in raw_errors)
    if is_json_invalid:
        return error_response(
            error="bad_request",
            message="Некорректный формат JSON в теле запроса",
            status_code=status.HTTP_400_BAD_REQUEST,
            details={"errors": sanitized},
        )

    return error_response(
        error="validation_error",
        message="Ошибка валидации входных параметров",
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        details={"errors": sanitized},
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return error_response(
        error=f"http_{exc.status_code}",
        message=str(exc.detail),
        status_code=exc.status_code,
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # В лог — только тип исключения: текст может содержать PII/секреты,
    # им не место в логах (см. AGENTS.md п.3 и guard no_pii_in_logs).
    logger.error("unhandled_exception", error=type(exc).__name__)
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
