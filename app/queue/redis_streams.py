import json
from typing import Any, Dict, List, Optional, Tuple
import redis.asyncio as aioredis
from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("redis_queue")

STREAM_LONG_JOBS = "jobs:audio:long"
STREAM_CALLBACKS = "jobs:callbacks"
GROUP_WORKERS = "group:workers"
GROUP_CALLBACKS = "group:callbacks"


class RedisQueue:
    def __init__(self):
        settings = get_settings()
        self.redis_url = settings.redis_url
        self.client: Optional[aioredis.Redis] = None

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
            (STREAM_CALLBACKS, GROUP_CALLBACKS),
        ]:
            try:
                await client.xgroup_create(stream, group, id="0", mkstream=True)
                logger.info("redis_group_created", stream=stream, group=group)
            except Exception as e:
                # Group already exists
                pass

    async def enqueue_long_job(self, job_id: str, data: Dict[str, Any]) -> str:
        client = await self.get_client()
        payload = {
            "job_id": job_id,
            "data": json.dumps(data, ensure_ascii=False),
        }
        msg_id = await client.xadd(STREAM_LONG_JOBS, payload)
        logger.info("job_enqueued", job_id=job_id, stream=STREAM_LONG_JOBS, msg_id=msg_id)
        return msg_id

    async def enqueue_callback(self, job_id: str, callback_url: str, payload: Dict[str, Any]) -> str:
        client = await self.get_client()
        msg_payload = {
            "job_id": job_id,
            "callback_url": callback_url,
            "payload": json.dumps(payload, ensure_ascii=False),
        }
        msg_id = await client.xadd(STREAM_CALLBACKS, msg_payload)
        logger.info("callback_enqueued", job_id=job_id, callback_url=callback_url, msg_id=msg_id)
        return msg_id

    async def read_next_job(
        self,
        stream: str,
        group: str,
        consumer: str,
        block_ms: int = 2000,
    ) -> Optional[Tuple[str, str, Dict[str, Any]]]:
        client = await self.get_client()
        try:
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
            return msg_id, job_id, parsed_data
        except Exception as e:
            logger.error("redis_read_error", error=str(e), stream=stream)
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


_queue_instance: Optional[RedisQueue] = None


def get_queue() -> RedisQueue:
    global _queue_instance
    if _queue_instance is None:
        _queue_instance = RedisQueue()
    return _queue_instance
