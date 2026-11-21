from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.models import Analysis, Call, Job, TranscriptSegment
from app.main import app
from tests.conftest import TestingSessionLocal


async def _seed_call(
    *,
    call_id: str,
    namespace: str,
    segments: list[tuple[str, str, str]],
) -> None:
    """Seeds a Call and its TranscriptSegments: (speaker_role, text_original, text_masked)."""
    async with TestingSessionLocal() as db:
        db.add(
            Call(
                id=call_id,
                namespace=namespace,
                duration_sec=45.0,
                is_answering_machine=False,
            )
        )
        for idx, (role, raw, masked) in enumerate(segments, start=1):
            db.add(
                TranscriptSegment(
                    call_id=call_id,
                    namespace=namespace,
                    segment_order=idx,
                    start_time=float((idx - 1) * 5),
                    end_time=float(idx * 5),
                    speaker_label="spk_0" if role == "manager" else "spk_1",
                    speaker_role=role,
                    text_original=raw,
                    text_masked=masked,
                    confidence=0.95,
                )
            )
        await db.commit()


@pytest.mark.asyncio
async def test_reprocess_call_pii_remasks_false_positives():
    """POST /v1/calls/{id}/reprocess with steps=['pii'] restores wrongly masked prices."""
    await _seed_call(
        call_id="call-pii-1",
        namespace="ns_reproc",
        segments=[
            (
                "manager",
                "Здравствуйте, Ольга, Представлюсь. Меня зовут Александр.",
                "Здравствуйте, [ИМЯ_3], [ИМЯ_2]. Меня зовут [ИМЯ_1].",
            ),
            (
                "client",
                "То есть я тогда открыл вместо Пяти, то случайно пятьдесят.",
                "То есть я тогда открыл вместо [ИМЯ_8], то случайно пятьдесят.",
            ),
        ],
    )

    headers = {"Authorization": "Bearer dev-token"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/v1/calls/call-pii-1/reprocess?namespace=ns_reproc",
            json={"steps": ["pii"]},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "succeeded"
        assert data["total_segments"] == 2
        assert data["updated_segments"] == 2

        tr_resp = await ac.get(
            "/v1/calls/call-pii-1/transcript?namespace=ns_reproc&masked=true",
            headers=headers,
        )
        assert tr_resp.status_code == 200
        segs = tr_resp.json()["segments"]
        assert "Представлюсь" in segs[0]["text"]
        assert "Ольга" not in segs[0]["text"]
        assert "Александр" not in segs[0]["text"]
        assert segs[1]["text"] == "То есть я тогда открыл вместо Пяти, то случайно пятьдесят."


@pytest.mark.asyncio
async def test_reprocess_call_analysis_sync_rules_mode():
    """POST /v1/calls/{id}/reprocess with steps=['pii', 'analysis'] updates Analysis."""
    await _seed_call(
        call_id="call-ana-1",
        namespace="ns_reproc",
        segments=[
            ("manager", "Добрый день! Чем могу помочь?", "Добрый день! Чем могу помочь?"),
            (
                "client",
                "Нужны билборды 3х6 в Москве на май, бюджет 200 000 рублей.",
                "Нужны билборды 3х6 в Москве на май, бюджет 200 000 рублей.",
            ),
        ],
    )

    headers = {"Authorization": "Bearer dev-token"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/v1/calls/call-ana-1/reprocess?namespace=ns_reproc",
            json={"steps": ["pii", "analysis"], "async_mode": False},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "succeeded"
        assert data["inquiry_type"] is not None

        an_resp = await ac.get(
            "/v1/calls/call-ana-1/analysis?namespace=ns_reproc",
            headers=headers,
        )
        assert an_resp.status_code == 200
        assert an_resp.json()["summary"]


@pytest.mark.asyncio
async def test_reprocess_call_async_mode_enqueues_job(monkeypatch):
    """When async_mode=True, endpoint enqueues a reprocess job and returns 202."""
    await _seed_call(
        call_id="call-async-1",
        namespace="ns_reproc",
        segments=[("client", "Привет, хочу щит", "Привет, хочу щит")],
    )

    mock_queue = MagicMock()
    mock_queue.enqueue_long_job = AsyncMock(return_value="1-0")
    monkeypatch.setattr("app.queue.redis_streams.get_queue", lambda: mock_queue)

    headers = {"Authorization": "Bearer dev-token"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/v1/calls/call-async-1/reprocess?namespace=ns_reproc",
            json={"steps": ["pii", "analysis"], "async_mode": True},
            headers=headers,
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "queued"
        assert data["job_id"]
        mock_queue.enqueue_long_job.assert_awaited_once()
        enqueued_job_id, enqueued_payload = mock_queue.enqueue_long_job.call_args.args
        assert enqueued_job_id == data["job_id"]
        assert enqueued_payload["task_type"] == "reprocess"
        assert enqueued_payload["call_id"] == "call-async-1"


@pytest.mark.asyncio
async def test_reprocess_call_namespace_isolation_and_auth():
    await _seed_call(
        call_id="call-iso-1",
        namespace="ns_alpha",
        segments=[("client", "Тест", "Тест")],
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        unauth = await ac.post(
            "/v1/calls/call-iso-1/reprocess?namespace=ns_alpha",
            json={"steps": ["pii"]},
        )
        assert unauth.status_code == 401

        wrong_ns = await ac.post(
            "/v1/calls/call-iso-1/reprocess?namespace=ns_beta",
            json={"steps": ["pii"]},
            headers={"Authorization": "Bearer dev-token"},
        )
        assert wrong_ns.status_code == 404


@pytest.mark.asyncio
async def test_admin_reprocess_namespace_batch():
    await _seed_call(
        call_id="call-batch-1",
        namespace="ns_batch",
        segments=[("client", "Вместо Пяти рублей", "Вместо [ИМЯ_1] рублей")],
    )
    await _seed_call(
        call_id="call-other-ns",
        namespace="ns_other",
        segments=[("client", "Вместо Пяти рублей", "Вместо [ИМЯ_1] рублей")],
    )

    headers = {"Authorization": "Bearer dev-token"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/v1/admin/reprocess-namespace",
            json={"namespace": "ns_batch", "steps": ["pii"]},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_calls"] == 1
        assert data["processed_calls"] == 1
        assert data["updated_segments"] == 1
        assert data["failed_calls"] == []

    # Verify other namespace was untouched
    async with TestingSessionLocal() as db:
        seg_other = (
            await db.execute(
                select(TranscriptSegment).where(TranscriptSegment.call_id == "call-other-ns")
            )
        ).scalar_one()
        assert seg_other.text_masked == "Вместо [ИМЯ_1] рублей"


@pytest.mark.asyncio
async def test_worker_process_reprocess_job(monkeypatch):
    """process_job dispatches task_type='reprocess' without touching S3 or ASR."""
    from app.workers import long_audio_processor as worker

    await _seed_call(
        call_id="call-worker-reproc",
        namespace="ns_worker",
        segments=[("client", "А-а, нужно три щита в Москве", "[ИМЯ_1], нужно три щита в Москве")],
    )
    async with TestingSessionLocal() as db:
        job = Job(
            id="job-reproc-1",
            namespace="ns_worker",
            call_id="call-worker-reproc",
            status="queued",
            progress=0,
            step="reprocess_queued",
        )
        db.add(job)
        await db.commit()

    monkeypatch.setattr(worker, "AsyncSessionLocal", TestingSessionLocal)

    await worker.process_job(
        "job-reproc-1",
        {
            "task_type": "reprocess",
            "call_id": "call-worker-reproc",
            "namespace": "ns_worker",
            "steps": ["pii", "analysis"],
        },
    )

    async with TestingSessionLocal() as db:
        j = (await db.execute(select(Job).where(Job.id == "job-reproc-1"))).scalar_one()
        assert j.status == "succeeded"
        assert j.progress == 100

        seg = (
            await db.execute(
                select(TranscriptSegment).where(TranscriptSegment.call_id == "call-worker-reproc")
            )
        ).scalar_one()
        assert seg.text_masked == "А-а, нужно три щита в Москве"

        an = (
            await db.execute(select(Analysis).where(Analysis.call_id == "call-worker-reproc"))
        ).scalar_one_or_none()
        assert an is not None
