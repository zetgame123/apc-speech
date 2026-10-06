import hashlib
import time

from fastapi import Request

from app.core.errors import AppError
from app.queue.redis_streams import get_queue


async def rate_limit(request: Request):
    """
    Simple Redis-based rate limiting dependency.
    Limits requests per window (default: 5 requests per 10 seconds per IP or token).
    Can be adjusted via RATE_LIMIT_REQUESTS_PER_WINDOW and RATE_LIMIT_WINDOW_SEC.
    Token is hashed to avoid leaking credentials into Redis keys.
    """
    from app.core.config import get_settings

    settings = get_settings()
    max_requests = settings.rate_limit_requests_per_window
    window_sec = settings.rate_limit_window_sec

    token = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    client_ip = request.client.host if request.client else "unknown"
    if token:
        token_hash = hashlib.sha256(token.encode()).hexdigest()[:16]
        key = f"rate_limit:tok:{token_hash}"
    else:
        key = f"rate_limit:ip:{client_ip}"

    current = int(time.time())
    window = current - (current % window_sec)
    redis_key = f"{key}:{window}"

    queue = get_queue()
    client = await queue.get_client()

    try:
        count = await client.incr(redis_key)
        if count == 1:
            await client.expire(redis_key, window_sec * 2)

        if count > max_requests:
            raise AppError(
                error="rate_limit_exceeded",
                message="Rate limit exceeded",
                status_code=429,
                headers={"Retry-After": str(window_sec)},
            )
    except AppError:
        raise
    except Exception as exc:
        # В production дорогие ручки fail-closed; локальная разработка и unit-тесты
        # не должны зависеть от запущенного Redis.
        if settings.app_env != "production":
            return
        raise AppError(
            error="rate_limiter_unavailable",
            message="Сервис ограничения нагрузки временно недоступен",
            status_code=503,
            details={"reason": type(exc).__name__},
            headers={"Retry-After": "5"},
        ) from exc
