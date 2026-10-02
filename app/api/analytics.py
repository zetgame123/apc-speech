import csv
import io
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.clustering import TopicClusteringEngine
from app.api.rate_limit import rate_limit
from app.core.errors import AppError
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
    auth.verify_namespace(namespace)

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

    cities_counter = defaultdict(int)
    formats_counter = defaultdict(int)

    chk_metrics = {
        "greeting": 0,
        "need_identification": 0,
        "options_offered": 0,
        "next_step_named": 0,
        "contacts_taken": 0,
    }
    chk_count = 0

    weekly_trends_dict = defaultdict(int)

    for c in calls:
        if c.created_at:
            week_str = f"{c.created_at.isocalendar()[0]}-W{c.created_at.isocalendar()[1]:02d}"
            weekly_trends_dict[week_str] += 1

    if call_ids:
        analysis_stmt = select(Analysis).where(Analysis.call_id.in_(call_ids))
        analyses = (await db.execute(analysis_stmt)).scalars().all()

        for a in analyses:
            itype = a.inquiry_type or "other"
            inquiry_dist[itype] = inquiry_dist.get(itype, 0) + 1

            if a.entities:
                for city in a.entities.get("cities", []):
                    cities_counter[city] += 1
                for fmt in a.entities.get("formats", []):
                    formats_counter[fmt] += 1

            if a.manager_checklist:
                chk_count += 1
                if "score" in a.manager_checklist:
                    checklist_scores.append(a.manager_checklist["score"])
                for key in chk_metrics:
                    if a.manager_checklist.get(key, False):
                        chk_metrics[key] += 1

    avg_score = round(sum(checklist_scores) / len(checklist_scores), 2) if checklist_scores else 0.0

    top_cities = [
        {"name": k, "count": v}
        for k, v in sorted(cities_counter.items(), key=lambda x: x[1], reverse=True)[:5]
    ]
    top_formats = [
        {"name": k, "count": v}
        for k, v in sorted(formats_counter.items(), key=lambda x: x[1], reverse=True)[:5]
    ]

    weekly_trends = [{"week": k, "calls_count": v} for k, v in sorted(weekly_trends_dict.items())]

    return {
        "total_calls": total_calls,
        "inquiry_distribution": inquiry_dist,
        "top_entities": {
            "cities": top_cities,
            "formats": top_formats,
        },
        "checklist_averages": {
            "average_score": avg_score,
            "greeting_pct": round(chk_metrics["greeting"] / chk_count * 100, 1)
            if chk_count
            else 0.0,
            "need_identification_pct": round(
                chk_metrics["need_identification"] / chk_count * 100, 1
            )
            if chk_count
            else 0.0,
            "options_offered_pct": round(chk_metrics["options_offered"] / chk_count * 100, 1)
            if chk_count
            else 0.0,
            "next_step_named_pct": round(chk_metrics["next_step_named"] / chk_count * 100, 1)
            if chk_count
            else 0.0,
            "contacts_taken_pct": round(chk_metrics["contacts_taken"] / chk_count * 100, 1)
            if chk_count
            else 0.0,
        },
        "weekly_trends": weekly_trends,
    }


@router.post("/texts/analyze")
async def analyze_texts(
    req: TextAnalyzeRequest,
    auth: AuthContext = Depends(verify_auth),
    _=Depends(rate_limit),
):
    auth.require_scope("write")
    auth.verify_namespace(req.namespace)

    if not req.texts:
        return {"total_texts": 0, "clusters": []}

    if len(req.texts) > 500:
        raise AppError(
            error="payload_too_large",
            message="Максимальное количество текстов для анализа за один запрос: 500",
            status_code=413,
        )

    for idx, text in enumerate(req.texts):
        if len(text) > 50000:
            raise AppError(
                error="payload_too_large",
                message=f"Текст #{idx + 1} превышает максимальную длину 50 000 символов",
                status_code=413,
            )

    labels, cluster_names, _ = clustering_engine.fit_transform_texts(req.texts)

    # Group texts by cluster
    grouped: dict[int, list[str]] = {}
    for text, label in zip(req.texts, labels):
        grouped.setdefault(label, []).append(text)

    clusters_output = []
    cid = 1
    for label, queries in grouped.items():
        name = cluster_names.get(label, f"Кластер {cid}")
        clusters_output.append(
            {
                "cluster_id": cid,
                "label": name,
                "size": len(queries),
                "representative_queries": queries[:3],
            }
        )
        cid += 1

    return {
        "total_texts": len(req.texts),
        "clusters": clusters_output,
    }


@router.get("/analytics/export_csv")
@router.get("/analytics/export.csv")
async def export_analytics_csv(
    namespace: str = Query(...),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    manager_id: str | None = Query(None),
    auth: AuthContext = Depends(verify_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require_scope("read")
    auth.verify_namespace(namespace)

    call_query = (
        select(Call, Analysis)
        .join(Analysis, Call.id == Analysis.call_id, isouter=True)
        .where(Call.namespace == namespace)
    )
    if manager_id:
        call_query = call_query.where(Call.manager_id == manager_id)
    if date_from:
        call_query = call_query.where(Call.created_at >= date_from)
    if date_to:
        call_query = call_query.where(Call.created_at <= date_to)

    results = (await db.execute(call_query)).all()

    output = io.StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(
        [
            "call_id",
            "external_id",
            "manager_id",
            "client_id",
            "created_at",
            "duration_sec",
            "is_answering_machine",
            "inquiry_type",
            "call_result",
            "summary",
            "checklist_score",
        ]
    )

    for call, analysis in results:
        created_str = (
            call.created_at.replace(tzinfo=timezone.utc).isoformat() if call.created_at else ""
        )
        writer.writerow(
            [
                call.id,
                call.external_id or "",
                call.manager_id or "",
                call.client_id or "",
                created_str,
                call.duration_sec or 0,
                "Yes" if call.is_answering_machine else "No",
                analysis.inquiry_type if analysis else "",
                analysis.call_result if analysis else "",
                analysis.summary if analysis else "",
                analysis.manager_checklist.get("score", "")
                if analysis and analysis.manager_checklist
                else "",
            ]
        )

    return Response(
        content=output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="export.csv"'},
    )
