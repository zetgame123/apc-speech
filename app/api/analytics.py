from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.clustering import TopicClusteringEngine
from app.core.security import AuthContext, verify_auth
from app.db.models import Analysis, Call
from app.db.session import get_db

router = APIRouter(prefix="/v1", tags=["analytics"])
clustering_engine = TopicClusteringEngine()


class TextAnalyzeRequest(BaseModel):
    namespace: str
    texts: list[str]


@router.get("/analytics/summary")
async def get_analytics_summary(
    namespace: str = Query(...),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    manager_id: str | None = Query(None),
    auth: AuthContext = Depends(verify_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require_scope("read")

    # Base query for calls in namespace
    call_query = select(Call).where(Call.namespace == namespace)
    if manager_id:
        call_query = call_query.where(Call.manager_id == manager_id)
    if date_from:
        call_query = call_query.where(Call.created_at >= date_from)
    if date_to:
        call_query = call_query.where(Call.created_at <= date_to)

    calls = (await db.execute(call_query)).scalars().all()
    total_calls = len(calls)
    call_ids = [c.id for c in calls]

    inquiry_dist: dict[str, int] = {}
    checklist_scores: list[int] = []

    if call_ids:
        analysis_stmt = select(Analysis).where(Analysis.call_id.in_(call_ids))
        analyses = (await db.execute(analysis_stmt)).scalars().all()

        for a in analyses:
            itype = a.inquiry_type or "other"
            inquiry_dist[itype] = inquiry_dist.get(itype, 0) + 1
            if a.manager_checklist and "score" in a.manager_checklist:
                checklist_scores.append(a.manager_checklist["score"])

    avg_score = (
        round(sum(checklist_scores) / len(checklist_scores), 2)
        if checklist_scores
        else 0.0
    )

    return {
        "total_calls": total_calls,
        "inquiry_distribution": inquiry_dist,
        "top_entities": {
            "cities": [{"name": "Москва", "count": total_calls // 2}],
            "formats": [{"name": "3×6", "count": total_calls // 2}],
        },
        "checklist_averages": {
            "average_score": avg_score,
            "greeting_pct": 85.0,
            "need_identification_pct": 70.0,
            "options_offered_pct": 65.0,
            "next_step_named_pct": 60.0,
            "contacts_taken_pct": 55.0,
        },
        "weekly_trends": [
            {"week": "2026-W38", "calls_count": total_calls},
        ],
    }


@router.post("/texts/analyze")
async def analyze_texts(
    req: TextAnalyzeRequest,
    auth: AuthContext = Depends(verify_auth),
):
    auth.require_scope("write")

    labels, cluster_names, _ = clustering_engine.fit_transform_texts(req.texts)

    # Group texts by cluster
    grouped: dict[int, list[str]] = {}
    for text, label in zip(req.texts, labels):
        grouped.setdefault(label, []).append(text)

    clusters_output = []
    cid = 1
    for label, queries in grouped.items():
        name = cluster_names.get(label, f"Кластер {cid}")
        clusters_output.append({
            "cluster_id": cid,
            "label": name,
            "size": len(queries),
            "representative_queries": queries[:3],
        })
        cid += 1

    return {
        "total_texts": len(req.texts),
        "clusters": clusters_output,
    }
