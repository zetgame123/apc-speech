#!/usr/bin/env python3
"""
S3 Inbox Watcher:
Monitors S3 raw audio bucket inbox prefix (e.g., 'inbox/' or 'inbox/<namespace>/'),
automatically registers processing jobs for newly uploaded audio files, moves them
into processing/calls storage, and enqueues them in Redis Streams.
"""

import argparse
import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.db.models import AudioObject, Call, Job
from app.db.session import AsyncSessionLocal
from app.queue.redis_streams import get_queue
from app.storage.s3 import get_storage

logger = get_logger("s3_inbox_watcher")


async def process_inbox_file(
    storage,
    bucket: str,
    key: str,
    file_size: int,
    default_namespace: str = "default",
    language: str = "ru",
):
    """
    Processes a single file discovered in inbox/ prefix.
    Key pattern: inbox/[<namespace>/]<filename>
    """
    settings = get_settings()
    parts = key.split("/")
    # Check if namespace is in key path: inbox/<namespace>/<filename>
    if len(parts) >= 3 and parts[0] == "inbox":
        namespace = parts[1]
        filename = "/".join(parts[2:])
    else:
        namespace = default_namespace
        filename = parts[-1]

    if not filename or filename.startswith("."):
        return

    job_id = str(uuid.uuid4())
    call_id = str(uuid.uuid4())
    ext = os.path.splitext(filename)[1] or ".wav"
    dst_key = f"calls/{call_id}/audio{ext}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    logger.info("ingesting_inbox_file", src_key=key, job_id=job_id, call_id=call_id, size=file_size)

    # 1. Move/Copy object from inbox/ to calls/
    if not storage.copy_object(bucket, key, bucket, dst_key):
        logger.error("failed_to_copy_s3_object", src=key, dst=dst_key)
        return

    # Delete source from inbox
    storage.delete_object(bucket, key)

    # 2. Register DB records
    async with AsyncSessionLocal() as db:
        call = Call(
            id=call_id,
            namespace=namespace,
            external_id=filename,
            direction="inbound",
            created_at=now,
            expires_at=now + timedelta(days=settings.retention_text_days),
        )
        db.add(call)

        audio_obj = AudioObject(
            id=str(uuid.uuid4()),
            namespace=namespace,
            call_id=call_id,
            s3_bucket=bucket,
            s3_key=dst_key,
            file_size_bytes=file_size,
            created_at=now,
            expires_at=now + timedelta(days=settings.retention_audio_days),
        )
        db.add(audio_obj)

        job = Job(
            id=job_id,
            namespace=namespace,
            call_id=call_id,
            status="queued",
            progress=0,
            step="pending_asr",
            idempotency_key=f"inbox:{key}",
            created_at=now,
        )
        db.add(job)
        await db.commit()

    # 3. Enqueue job into Redis Streams
    queue = get_queue()
    payload = {
        "job_id": job_id,
        "call_id": call_id,
        "namespace": namespace,
        "s3_bucket": bucket,
        "s3_key": dst_key,
        "language": language,
        "metadata": {
            "source": "s3_inbox",
            "original_filename": filename,
            "original_key": key,
        },
    }
    await queue.enqueue_long_job(job_id, payload)
    logger.info("inbox_job_enqueued_successfully", job_id=job_id, call_id=call_id)


async def scan_inbox(
    bucket: str, prefix: str = "inbox/", namespace: str = "default", language: str = "ru"
):
    storage = get_storage()
    objects = storage.list_objects(bucket, prefix=prefix)
    valid_objects = [o for o in objects if o["key"] != prefix and o["size"] > 0]

    if valid_objects:
        logger.info("inbox_objects_detected", count=len(valid_objects))

    for obj in valid_objects:
        try:
            await process_inbox_file(
                storage=storage,
                bucket=bucket,
                key=obj["key"],
                file_size=obj["size"],
                default_namespace=namespace,
                language=language,
            )
        except Exception as e:
            logger.error("error_processing_inbox_file", key=obj["key"], error=str(e))


async def main():
    parser = argparse.ArgumentParser(description="APC-Speech S3 Inbox Ingestion Watcher")
    parser.add_argument(
        "--interval", type=int, default=10, help="Poll interval in seconds (default: 10)"
    )
    parser.add_argument(
        "--namespace", type=str, default="default", help="Default namespace (default: default)"
    )
    parser.add_argument(
        "--prefix", type=str, default="inbox/", help="S3 inbox prefix (default: inbox/)"
    )
    parser.add_argument(
        "--bucket", type=str, default=None, help="S3 bucket name (default from settings)"
    )
    parser.add_argument("--language", type=str, default="ru", help="Speech language (default: ru)")
    parser.add_argument("--once", action="store_true", help="Run scan once and exit")
    args = parser.parse_args()

    settings = get_settings()
    setup_logging(settings.log_level)
    bucket = args.bucket or settings.s3_bucket_audio

    logger.info(
        "s3_inbox_watcher_started",
        bucket=bucket,
        prefix=args.prefix,
        interval=args.interval,
        once=args.once,
    )

    if args.once:
        await scan_inbox(
            bucket, prefix=args.prefix, namespace=args.namespace, language=args.language
        )
        return

    while True:
        try:
            await scan_inbox(
                bucket, prefix=args.prefix, namespace=args.namespace, language=args.language
            )
        except Exception as e:
            logger.error("inbox_scan_failed", error=str(e))
        await asyncio.sleep(args.interval)


if __name__ == "__main__":
    asyncio.run(main())
