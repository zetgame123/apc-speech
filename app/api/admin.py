from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthContext, verify_auth
from app.db.models import AudioObject, Call
from app.db.session import get_db
from app.storage.s3 import get_storage

router = APIRouter(prefix="/v1/data", tags=["admin"])


@router.delete("/delete")
async def delete_data(
    namespace: str = Query(...),
    call_id: str | None = Query(None),
    client_id: str | None = Query(None),
    date_before: datetime | None = Query(None),
    auth: AuthContext = Depends(verify_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require_scope("admin")

    # Select target calls
    query = select(Call).where(Call.namespace == namespace)
    if call_id:
        query = query.where(Call.id == call_id)
    if client_id:
        query = query.where(Call.client_id == client_id)
    if date_before:
        query = query.where(Call.created_at <= date_before)

    calls = (await db.execute(query)).scalars().all()
    target_call_ids = [c.id for c in calls]

    if not target_call_ids:
        return {
            "deleted_calls": 0,
            "deleted_audio_files": 0,
            "deleted_transcripts": 0,
        }

    # Delete audio objects from S3
    storage = get_storage()
    audio_stmt = select(AudioObject).where(AudioObject.call_id.in_(target_call_ids))
    audio_objs = (await db.execute(audio_stmt)).scalars().all()

    deleted_files_count = 0
    for a in audio_objs:
        if storage.delete_object(a.s3_bucket, a.s3_key):
            deleted_files_count += 1

    # Cascade delete calls from DB
    delete_stmt = delete(Call).where(Call.id.in_(target_call_ids))
    await db.execute(delete_stmt)
    await db.commit()

    return {
        "deleted_calls": len(target_call_ids),
        "deleted_audio_files": deleted_files_count,
        "deleted_transcripts": len(target_call_ids),
    }
