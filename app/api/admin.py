import json
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    AuthContext,
    list_revoked_tokens,
    revoke_token,
    verify_auth,
)
from app.db.models import AudioObject, Call, Job
from app.db.session import get_db
from app.storage.s3 import get_storage

router = APIRouter(tags=["admin"])


# --- Token revocation (hot, without restart) ---


class RevokeTokenRequest(BaseModel):
    token_hash: str


@router.post("/v1/admin/tokens/revoke")
async def revoke_token_endpoint(
    body: RevokeTokenRequest,
    auth: AuthContext = Depends(verify_auth),
):
    auth.require_scope("admin")
    was_new = revoke_token(body.token_hash)
    try:
        from app.queue.redis_streams import get_queue

        client = await get_queue().get_client()
        await client.sadd("revoked_tokens", body.token_hash)
    except Exception:
        pass
    return {"token_hash": body.token_hash, "revoked": True, "was_new": was_new}


@router.get("/v1/admin/tokens/revoked")
async def list_revoked_tokens_endpoint(
    auth: AuthContext = Depends(verify_auth),
):
    auth.require_scope("admin")
    revoked = set(list_revoked_tokens())
    try:
        from app.queue.redis_streams import get_queue

        client = await get_queue().get_client()
        redis_revoked = await client.smembers("revoked_tokens")
        if redis_revoked:
            for r in redis_revoked:
                token_h = r.decode() if isinstance(r, bytes) else str(r)
                revoked.add(token_h)
                revoke_token(token_h)
    except Exception:
        pass
    return {"revoked_hashes": list(revoked)}


# --- Data deletion ---


@router.delete("/v1/data/delete")
async def delete_data(
    namespace: str = Query(...),
    call_id: str | None = Query(None),
    participant_id: str | None = Query(None),
    client_id: str | None = Query(None),
    manager_id: str | None = Query(None),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    date_before: datetime | None = Query(None, deprecated=True),
    auth: AuthContext = Depends(verify_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require_scope("admin")
    auth.verify_namespace(namespace)

    # Select target calls
    query = select(Call).where(Call.namespace == namespace)
    if call_id:
        query = query.where(Call.id == call_id)
    if participant_id:
        query = query.where(
            (Call.client_id == participant_id) | (Call.manager_id == participant_id)
        )
    if client_id:
        query = query.where(Call.client_id == client_id)
    if manager_id:
        query = query.where(Call.manager_id == manager_id)
    if date_from:
        query = query.where(Call.created_at >= date_from)
    if date_to:
        query = query.where(Call.created_at <= date_to)
    if date_before:
        query = query.where(Call.created_at <= date_before)

    calls = (await db.execute(query)).scalars().all()
    target_call_ids = [c.id for c in calls]

    # Clean up voice query history from Redis for GDPR compliance
    deleted_voice_queries = 0
    if not call_id and not participant_id and not client_id and not manager_id:
        try:
            from app.queue.redis_streams import get_queue

            client = await get_queue().get_client()
            effective_date_to = date_to or date_before
            if not date_from and not effective_date_to:
                deleted_voice_queries = await client.delete(f"voice:history:{namespace}")
            else:
                raw_items = await client.lrange(f"voice:history:{namespace}", 0, -1)
                to_keep = []
                for raw in raw_items:
                    try:
                        decoded = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                        item = json.loads(decoded)
                        item_dt = datetime.fromisoformat(item.get("created_at"))
                        in_range = (date_from is None or item_dt >= date_from) and (
                            effective_date_to is None or item_dt <= effective_date_to
                        )
                        if in_range:
                            deleted_voice_queries += 1
                        else:
                            to_keep.append(raw)
                    except Exception:
                        to_keep.append(raw)
                await client.delete(f"voice:history:{namespace}")
                if to_keep:
                    await client.rpush(f"voice:history:{namespace}", *to_keep)
        except Exception:
            pass

    if not target_call_ids:
        return {
            "deleted_calls": 0,
            "deleted_audio_files": 0,
            "deleted_transcripts": 0,
            "deleted_voice_queries": deleted_voice_queries,
        }

    # Cancel any active/queued jobs for these calls
    cancel_jobs_stmt = (
        update(Job)
        .where(
            Job.call_id.in_(target_call_ids),
            Job.status.in_(["queued", "running"]),
        )
        .values(
            status="failed",
            step="cancelled_by_deletion",
            error_details={
                "error": "cancelled_by_deletion",
                "message": "Данные звонка удалены администратором",
            },
        )
    )
    await db.execute(cancel_jobs_stmt)

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
        "deleted_voice_queries": deleted_voice_queries,
    }
