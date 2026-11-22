import asyncio
import csv
import io
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.clustering import TopicClusteringEngine
from app.api.rate_limit import rate_limit
from app.core.errors import AppError
from app.core.security import AuthContext, verify_auth
from app.db.models import Analysis, Call
from app.db.session import get_db
from app.pii.masker import get_pii_masker

router = APIRouter(prefix="/v1", tags=["analytics"])
clustering_engine = TopicClusteringEngine()


def _naive(dt: datetime | None) -> datetime | None:
    # Call.created_at хранится в БД как naive UTC; aware-даты сначала приводим к UTC.
    if dt is not None and dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


class TextAnalyzeRequest(BaseModel):
    namespace: str
    texts: list[str]


@router.get("/analytics/summary")
async def get_analytics_summary(
    namespace: str = Query(...),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    manager_id: str | None = Query(None),
    inquiry_type: str | None = Query(None),
    auth: AuthContext = Depends(verify_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require_scope("read")
    auth.verify_namespace(namespace)

    filters = [Call.namespace == namespace]
    if manager_id:
        filters.append(Call.manager_id == manager_id)
    if date_from:
        filters.append(Call.created_at >= _naive(date_from))
    if date_to:
        filters.append(Call.created_at <= _naive(date_to))

    use_inner_join = bool(inquiry_type)
    if inquiry_type:
        filters.append(Analysis.inquiry_type == inquiry_type)

    # 1. SQL COUNT for total_calls (no ORM hydration)
    count_q = select(func.count(Call.id)).select_from(Call)
    if use_inner_join:
        count_q = count_q.join(Analysis, Analysis.call_id == Call.id)
    total_calls = int((await db.execute(count_q.where(*filters))).scalar_one())

    # 2. SQL GROUP BY for inquiry_distribution
    dist_q = (
        select(
            func.coalesce(Analysis.inquiry_type, "other").label("itype"),
            func.count(Analysis.id),
        )
        .select_from(Call)
        .join(Analysis, Analysis.call_id == Call.id)
        .where(*filters)
        .group_by("itype")
    )
    inquiry_dist: dict[str, int] = {row[0]: int(row[1]) for row in (await db.execute(dist_q)).all()}

    # 3. Single lightweight column-projection query with LEFT/INNER JOIN + yield_per
    # Avoids loading full Call/Analysis ORM rows and avoids IN (call_ids) parameter limits.
    proj_q = (
        select(Call.created_at, Analysis.entities, Analysis.manager_checklist)
        .select_from(Call)
        .join(Analysis, Analysis.call_id == Call.id, isouter=not use_inner_join)
        .where(*filters)
        .execution_options(yield_per=1000)
    )

    checklist_score_sum = 0.0
    checklist_score_count = 0
    cities_counter: dict[str, int] = defaultdict(int)
    formats_counter: dict[str, int] = defaultdict(int)
    chk_metrics = {
        "greeting": 0,
        "need_identification": 0,
        "options_offered": 0,
        "next_step_named": 0,
        "contacts_taken": 0,
    }
    chk_count = 0
    weekly_trends_dict: dict[str, int] = defaultdict(int)

    result_stream = await db.stream(proj_q)
    async for created_at, entities, checklist in result_stream:
        if created_at:
            iso = created_at.isocalendar()
            weekly_trends_dict[f"{iso[0]}-W{iso[1]:02d}"] += 1

        if entities:
            for city in entities.get("cities", []):
                cities_counter[city] += 1
            for fmt in entities.get("formats", []):
                formats_counter[fmt] += 1

        if checklist:
            chk_count += 1
            if "score" in checklist and checklist["score"] is not None:
                checklist_score_sum += checklist["score"]
                checklist_score_count += 1
            for key in chk_metrics:
                if checklist.get(key, False):
                    chk_metrics[key] += 1

    avg_score = (
        round(checklist_score_sum / checklist_score_count, 2) if checklist_score_count else 0.0
    )

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

    # C7: кластеризация и весь ответ — только по маскированным текстам.
    # Иначе сырые ПД утекают в подписи кластеров (топ-термы вроде "1111")
    # и в представителей, даже если представители маскировать отдельно.
    masker = get_pii_masker()
    masked_texts = [masker.mask_text(t) for t in req.texts]
    labels, cluster_names, _ = await asyncio.to_thread(
        clustering_engine.fit_transform_texts, masked_texts
    )

    # Group texts by cluster
    grouped: dict[int, list[str]] = {}
    for text, label in zip(masked_texts, labels):
        grouped.setdefault(label, []).append(text)

    clusters_output = []
    cid = 1
    sorted_groups = sorted(
        grouped.items(),
        key=lambda item: (item[0] == -1, -len(item[1])),
    )
    for label, queries in sorted_groups:
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
    inquiry_type: str | None = Query(None),
    auth: AuthContext = Depends(verify_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require_scope("read")
    auth.verify_namespace(namespace)

    call_query = (
        select(
            Call.id,
            Call.external_id,
            Call.manager_id,
            Call.client_id,
            Call.created_at,
            Call.duration_sec,
            Call.is_answering_machine,
            Analysis.inquiry_type,
            Analysis.call_result,
            Analysis.summary,
            Analysis.manager_checklist,
        )
        .select_from(Call)
        .join(Analysis, Call.id == Analysis.call_id, isouter=True)
        .where(Call.namespace == namespace)
    )
    if manager_id:
        call_query = call_query.where(Call.manager_id == manager_id)
    if inquiry_type:
        call_query = call_query.where(Analysis.inquiry_type == inquiry_type)
    if date_from:
        call_query = call_query.where(Call.created_at >= _naive(date_from))
    if date_to:
        call_query = call_query.where(Call.created_at <= _naive(date_to))

    call_query = call_query.execution_options(yield_per=1000)

    def _csv_safe(val: object) -> object:
        if isinstance(val, str) and val.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
            return "'" + val
        return val

    async def _csv_stream():
        buf = io.StringIO()
        writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
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
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)

        stream = await db.stream(call_query)
        async for row in stream:
            (
                call_id,
                external_id,
                mgr_id,
                cli_id,
                created_at,
                duration_sec,
                is_am,
                a_inquiry,
                a_result,
                a_summary,
                a_checklist,
            ) = row
            created_str = created_at.replace(tzinfo=timezone.utc).isoformat() if created_at else ""
            writer.writerow(
                [
                    _csv_safe(call_id),
                    _csv_safe(external_id or ""),
                    _csv_safe(mgr_id or ""),
                    _csv_safe(cli_id or ""),
                    created_str,
                    duration_sec or 0,
                    "Yes" if is_am else "No",
                    _csv_safe(a_inquiry or ""),
                    _csv_safe(a_result or ""),
                    _csv_safe(a_summary or ""),
                    a_checklist.get("score", "") if a_checklist else "",
                ]
            )
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)

    return StreamingResponse(
        _csv_stream(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="export.csv"'},
    )
