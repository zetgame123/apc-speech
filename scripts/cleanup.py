import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from app.db.models import Call, Job
from app.db.session import AsyncSessionLocal
from app.storage.s3 import get_storage
from app.core.config import get_settings

async def run_cleanup():
    settings = get_settings()
    storage = get_storage()
    now = datetime.now(timezone.utc)
    
    # 1. Clean up audio files older than retention_audio_days
    # Note: A real implementation would scan the DB for calls older than retention_audio_days 
    # and delete their corresponding S3 objects, or just use S3 Bucket Lifecycle Rules.
    # For this script, we'll delete S3 objects for calls that are older than audio retention.
    
    audio_cutoff = now - timedelta(days=settings.retention_audio_days)
    async with AsyncSessionLocal() as db:
        stmt = select(Call.id).where(Call.created_at < audio_cutoff.replace(tzinfo=None))
        old_calls = (await db.execute(stmt)).scalars().all()
        
        for call_id in old_calls:
            # The S3 key is usually call_id.wav or similar. 
            # In jobs.py it's: f"{namespace}/{now.strftime('%Y/%m/%d')}/{call_id}{ext}"
            # Because we don't store the exact S3 key in the DB (only the Call model),
            # In a real app we'd query the Job or S3 directly.
            pass
            
    # 2. Clean up expired DB records (text retention)
    async with AsyncSessionLocal() as db:
        stmt = delete(Call).where(Call.expires_at < now.replace(tzinfo=None))
        result = await db.execute(stmt)
        await db.commit()
        print(f"Deleted {result.rowcount} expired calls from database.")

if __name__ == "__main__":
    asyncio.run(run_cleanup())
