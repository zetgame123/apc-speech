import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.core.config import get_settings
from app.db.models import AudioObject, Call
from app.db.session import AsyncSessionLocal
from app.storage.s3 import get_storage


async def run_cleanup():
    settings = get_settings()
    storage = get_storage()
    now = datetime.now(timezone.utc)

    # 1. Clean up audio files older than retention_audio_days
    audio_cutoff = now - timedelta(days=settings.retention_audio_days)

    async with AsyncSessionLocal() as db:
        # We query audio objects that are older than the retention limit
        stmt = select(AudioObject).where(AudioObject.created_at < audio_cutoff.replace(tzinfo=None))
        old_audio_objs = (await db.execute(stmt)).scalars().all()

        deleted_audio = 0
        for audio in old_audio_objs:
            # Delete from S3
            success = storage.delete_object(bucket=audio.s3_bucket, key=audio.s3_key)
            if success:
                # Delete record from DB
                await db.delete(audio)
                deleted_audio += 1

        await db.commit()
        print(f"Deleted {deleted_audio} expired audio files from S3.")

    # 2. Clean up expired DB records (text retention)
    async with AsyncSessionLocal() as db:
        stmt = delete(Call).where(Call.expires_at < now.replace(tzinfo=None))
        result = await db.execute(stmt)
        await db.commit()
        print(f"Deleted {result.rowcount} expired calls from database.")

if __name__ == "__main__":
    asyncio.run(run_cleanup())
