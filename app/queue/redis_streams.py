import json
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("redis_queue")

STREAM_LONG_JOBS = "jobs:audio:long"
STREAM_FAST_JOBS = "jobs:audio:fast"
STREAM_CALLBACKS = "jobs:callbacks"
STREAM_DLQ = "jobs:audio:dlq"
GROUP_WORKERS = "group:workers"
GROUP_FAST_WORKERS = "group:fast-workers"
GROUP_CALLBACKS = "group:callbacks"


class RedisQueue:
    def __init__(self):
        settings = get_settings()
        self.redis_url = settings.redis_url
        self.client: aioredis.Redis | None = None

    async def get_client(self) -> aioredis.Redis:
        if self.client is None:
            self.client = aioredis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
        return self.client

    async def ensure_groups(self):
        client = await self.get_client()
        for stream, group in [
            (STREAM_LONG_JOBS, GROUP_WORKERS),
            (STREAM_FAST_JOBS, GROUP_FAST_WORKERS),
            (STREAM_CALLBACKS, GROUP_CALLBACKS),
        ]:
            try:
                await client.xgroup_create(stream, group, id="0", mkstream=True)
                logger.info("redis_group_created", stream=stream, group=group)
            except Exception:
                # Group already exists
                pass

    async def enqueue_long_job(self, job_id: str, data: dict[str, Any]) -> str:
        client = await self.get_client()
        payload = {
            "job_id": job_id,
            "data": json.dumps(data, ensure_ascii=False),
        }
        msg_id = await client.xadd(STREAM_LONG_JOBS, payload)
        logger.info("job_enqueued", job_id=job_id, stream=STREAM_LONG_JOBS, msg_id=msg_id)
        return msg_id

    async def enqueue_fast_job(self, request_id: str, data: dict[str, Any]) -> str:
        client = await self.get_client()
        return await client.xadd(
            STREAM_FAST_JOBS,
            {"job_id": request_id, "data": json.dumps(data, ensure_ascii=False)},
        )

    async def wait_fast_result(self, request_id: str, timeout_sec: float) -> dict[str, Any] | None:
        client = await self.get_client()
        key = f"voice:result:{request_id}"
        result = await client.blpop(key, timeout=max(1, int(timeout_sec + 0.999)))
        if not result:
            return None
        raw = result[1]
        await client.delete(key)
        return json.loads(raw)

    async def publish_fast_result(
        self, request_id: str, result: dict[str, Any], ttl_sec: int = 30
    ) -> None:
        client = await self.get_client()
        key = f"voice:result:{request_id}"
        await client.rpush(key, json.dumps(result, ensure_ascii=False))
        await client.expire(key, ttl_sec)

    async def enqueue_callback(
        self, job_id: str, callback_url: str, payload: dict[str, Any]
    ) -> str:
        client = await self.get_client()
        # Guarantee callback_url and job_id exist both at top-level and inside payload
        payload_with_url = dict(payload)
        payload_with_url["callback_url"] = callback_url
        payload_with_url["job_id"] = job_id
        msg_payload = {
            "job_id": job_id,
            "callback_url": callback_url,
            "payload": json.dumps(payload_with_url, ensure_ascii=False),
        }
        msg_id = await client.xadd(STREAM_CALLBACKS, msg_payload)
        logger.info("callback_enqueued", job_id=job_id, callback_url=callback_url, msg_id=msg_id)
        return msg_id

    async def enqueue_dlq(self, job_id: str, error: dict[str, Any], payload: dict[str, Any]) -> str:
        client = await self.get_client()
        dlq_data = {
            "job_id": job_id,
            "error": json.dumps(error, ensure_ascii=False),
            "payload": json.dumps(payload, ensure_ascii=False),
            "failed_at": datetime.now(timezone.utc).isoformat(),
        }
        msg_id = await client.xadd(STREAM_DLQ, dlq_data)
        logger.warning("job_moved_to_dlq", job_id=job_id, stream=STREAM_DLQ, msg_id=msg_id)
        return msg_id

    async def read_next_job(
        self,
        stream: str,
        group: str,
        consumer: str,
        block_ms: int = 2000,
        claim_min_idle_ms: int = 180000,
    ) -> tuple[str, str, dict[str, Any]] | None:
        client = await self.get_client()
        try:
            # 1. Recover pending jobs from crashed workers via XAUTOCLAIM
            try:
                autoclaim_res = await client.xautoclaim(
                    name=stream,
                    groupname=group,
                    consumername=consumer,
                    min_idle_time=claim_min_idle_ms,
                    start_id="0-0",
                    count=1,
                )
                if autoclaim_res and len(autoclaim_res) >= 2 and autoclaim_res[1]:
                    reclaimed = autoclaim_res[1]
                    msg_id, fields = reclaimed[0]
                    job_id = fields.get("job_id", "")
                    raw_data = fields.get("data") or fields.get("payload") or "{}"
                    parsed_data = json.loads(raw_data)
                    if "callback_url" in fields and "callback_url" not in parsed_data:
                        parsed_data["callback_url"] = fields["callback_url"]
                    logger.info(
                        "reclaimed_stuck_job",
                        stream=stream,
                        group=group,
                        msg_id=msg_id,
                        job_id=job_id,
                    )
                    return msg_id, job_id, parsed_data
            except Exception as claim_err:
                logger.debug("xautoclaim_check_skipped", error=str(claim_err))

            # 2. Read new messages
            entries = await client.xreadgroup(
                groupname=group,
                consumername=consumer,
                streams={stream: ">"},
                count=1,
                block=block_ms,
            )
            if not entries:
                return None

            stream_name, messages = entries[0]
            if not messages:
                return None

            msg_id, fields = messages[0]
            job_id = fields.get("job_id", "")
            raw_data = fields.get("data") or fields.get("payload") or "{}"
            parsed_data = json.loads(raw_data)
            if "callback_url" in fields and "callback_url" not in parsed_data:
                parsed_data["callback_url"] = fields["callback_url"]
            return msg_id, job_id, parsed_data
        except Exception as e:
            err_str = str(e)
            logger.error("redis_read_error", error=err_str, stream=stream)
            if "NOGROUP" in err_str:
                try:
                    await self.ensure_groups()
                except Exception:
                    pass
            return None

    async def ack_job(self, stream: str, group: str, msg_id: str):
        client = await self.get_client()
        await client.xack(stream, group, msg_id)
        await client.xdel(stream, msg_id)

    async def check_health(self) -> bool:
        try:
            client = await self.get_client()
            return await client.ping()
        except Exception:
            return False


_queue_instance: RedisQueue | None = None


def get_queue() -> RedisQueue:
    global _queue_instance
    if _queue_instance is None:
        _queue_instance = RedisQueue()
    return _queue_instance
