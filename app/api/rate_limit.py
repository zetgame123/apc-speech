import hashlib
import time

from fastapi import Request

from app.core.errors import AppError
from app.queue.redis_streams import get_queue


async def rate_limit(request: Request):
    """
    Simple Redis-based rate limiting dependency.
    Limits to 5 requests per 10 seconds per IP or token.
    Token is hashed to avoid leaking credentials into Redis keys.
    """
    token = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    client_ip = request.client.host if request.client else "unknown"
    if token:
        token_hash = hashlib.sha256(token.encode()).hexdigest()[:16]
        key = f"rate_limit:tok:{token_hash}"
    else:
        key = f"rate_limit:ip:{client_ip}"

    current = int(time.time())
    window = current - (current % 10)
    redis_key = f"{key}:{window}"

    queue = get_queue()
    client = await queue.get_client()

    try:
        count = await client.incr(redis_key)
        if count == 1:
            await client.expire(redis_key, 20)

        if count > 5:
            raise AppError(
                error="rate_limit_exceeded",
                message="Rate limit exceeded",
                status_code=429,
                headers={"Retry-After": "10"},
            )
    except AppError:
        raise
    except Exception:
        # If redis is down, we don't want to block the app completely, just log and pass
        pass
