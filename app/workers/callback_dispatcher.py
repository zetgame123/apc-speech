import asyncio
import inspect
import json
import os
import signal

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.core.security import sign_callback_payload, validate_ssrf_url
from app.core.urls import redact_url_for_log
from app.db.models import CallbackLog
from app.db.session import AsyncSessionLocal
from app.queue.redis_streams import (
    GROUP_CALLBACKS,
    STREAM_CALLBACKS,
    get_queue,
)

settings = get_settings()
setup_logging(settings.log_level)
logger = get_logger("callback_dispatcher")

is_running = True


def handle_sigterm(*args):
    global is_running
    logger.info("callback_worker_sigterm")
    is_running = False


signal.signal(signal.SIGTERM, handle_sigterm)
signal.signal(signal.SIGINT, handle_sigterm)

RETRY_DELAYS_SEC = [5, 15, 60, 300, 900]  # 5 retry attempts with backoff


async def dispatch_callback(job_id: str, callback_url: str, payload: dict):
    if not callback_url:
        logger.error("callback_url_empty", job_id=job_id)
        return False

    # SSRF protection on outgoing callback URLs
    try:
        validate_ssrf_url(callback_url)
    except Exception as ssrf_err:
        logger.error(
            "callback_ssrf_rejected",
            job_id=job_id,
            url=redact_url_for_log(callback_url),
            error=str(ssrf_err),
        )
        return False

    raw_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    for attempt, delay in enumerate(RETRY_DELAYS_SEC, 1):
        try:
            # Recompute timestamp and signature for each attempt to prevent expiration (>300s)
            timestamp, signature = sign_callback_payload(settings.callback_signing_secret, raw_body)
            headers = {
                "Content-Type": "application/json",
                "X-Apc-Timestamp": str(timestamp),
                "X-Apc-Signature": signature,
            }

            logger.info(
                "dispatching_callback_attempt", job_id=job_id, url=callback_url, attempt=attempt
            )
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
                response = await client.post(callback_url, content=raw_body, headers=headers)

                # Log attempt in DB
                async with AsyncSessionLocal() as db:
                    cb_log = CallbackLog(
                        job_id=job_id,
                        url=callback_url,
                        attempt=attempt,
                        http_status=response.status_code,
                        response_body=response.text[:1000],
                    )
                    add_res = db.add(cb_log)
                    if inspect.isawaitable(add_res):
                        await add_res
                    await db.commit()

                if response.is_success:
                    logger.info("callback_delivered_successfully", job_id=job_id, attempt=attempt)
                    return True
                else:
                    logger.warning(
                        "callback_failed_status", status=response.status_code, attempt=attempt
                    )

        except Exception as e:
            logger.error("callback_exception", error=str(e), attempt=attempt)
            async with AsyncSessionLocal() as db:
                cb_log = CallbackLog(
                    job_id=job_id,
                    url=callback_url,
                    attempt=attempt,
                    http_status=None,
                    response_body=str(e)[:1000],
                )
                add_res = db.add(cb_log)
                if inspect.isawaitable(add_res):
                    await add_res
                await db.commit()

        # Wait before next attempt
        if attempt < len(RETRY_DELAYS_SEC):
            await asyncio.sleep(delay)

    logger.error("callback_all_attempts_exhausted", job_id=job_id)
    return False


async def main():
    queue = get_queue()
    await queue.ensure_groups()
    logger.info("callback_dispatcher_started", group=GROUP_CALLBACKS)

    while is_running:
        try:
            job_item = await queue.read_next_job(
                stream=STREAM_CALLBACKS,
                group=GROUP_CALLBACKS,
                consumer=f"cb_worker_{os.getpid()}",
                block_ms=3000,
            )
            if not job_item:
                continue

            msg_id, job_id, payload = job_item
            callback_url = payload.get("callback_url")
            callback_data = payload.get("payload") or payload

            if callback_url:
                await dispatch_callback(job_id, callback_url, callback_data)

            await queue.ack_job(STREAM_CALLBACKS, GROUP_CALLBACKS, msg_id)

        except Exception as loop_err:
            logger.error("callback_loop_error", error=str(loop_err))
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
