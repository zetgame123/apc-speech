import asyncio
import os
import signal
import tempfile
import traceback
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app.asr.engine import get_asr_engine
from app.asr.preprocessor import AudioPreprocessor
from app.asr.vad import VadDetector
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import get_logger, setup_logging
from app.core.security import download_file_ssrf_safe
from app.db.models import Analysis, AudioObject, Call, Job, TranscriptSegment
from app.db.session import AsyncSessionLocal
from app.diarization.roles import RoleClassifier
from app.integrations.bitrix24 import format_timeline_comment, post_to_crm_timeline
from app.llm.local_adapter import LocalLlamaCppAdapter
from app.llm.port import AnalysisContext, validate_analysis_schema
from app.metrics.speech_metrics import SpeechMetricsCalculator
from app.pii.masker import PiiMasker
from app.queue.redis_streams import (
    GROUP_WORKERS,
    STREAM_LONG_JOBS,
    get_queue,
)
from app.storage.s3 import get_storage

settings = get_settings()
setup_logging(settings.log_level)
logger = get_logger("long_worker")

is_running = True


def handle_sigterm(*args):
    global is_running
    logger.info("sigterm_received_stopping_gracefully")
    is_running = False


signal.signal(signal.SIGTERM, handle_sigterm)
signal.signal(signal.SIGINT, handle_sigterm)


async def process_job(job_id: str, data: dict):
    call_id = data["call_id"]
    namespace = data["namespace"]
    callback_url = data.get("callback_url")
    storage = get_storage()
    preprocessor = AudioPreprocessor()
    vad_detector = VadDetector()
    asr_engine = get_asr_engine()
    role_classifier = RoleClassifier()
    metrics_calc = SpeechMetricsCalculator()
    pii_masker = PiiMasker()
    llm_adapter = LocalLlamaCppAdapter()

    logger.info("starting_job_processing", job_id=job_id, call_id=call_id)

    async with AsyncSessionLocal() as db:
        # Update Job -> running (step: preprocessing)
        await db.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(status="running", progress=10, step="downloading_audio")
        )
        await db.commit()

    with tempfile.TemporaryDirectory() as tmp_dir:
        input_audio_path = os.path.join(tmp_dir, "input_raw_audio")

        # 1. Acquire audio: from S3 or by downloading URL
        if "s3_key" in data:
            storage.download_to_file(data["s3_bucket"], data["s3_key"], input_audio_path)
        elif "source_url" in data:
            max_bytes = settings.max_upload_size_mb * 1024 * 1024
            downloaded_hash = await download_file_ssrf_safe(
                url=data["source_url"],
                target_path=input_audio_path,
                max_bytes=max_bytes,
                timeout=60.0,
            )

            # Persist downloaded audio to S3 for replay and compliance
            s3_key = f"calls/{call_id}/audio.wav"
            s3_bucket = settings.s3_bucket_audio
            storage.upload_file(input_audio_path, s3_bucket, s3_key)
            now_dt = datetime.now(timezone.utc).replace(tzinfo=None)
            async with AsyncSessionLocal() as db:
                audio_obj = AudioObject(
                    id=str(uuid.uuid4()),
                    namespace=data.get("namespace", "default"),
                    call_id=call_id,
                    s3_bucket=s3_bucket,
                    s3_key=s3_key,
                    file_size_bytes=os.path.getsize(input_audio_path),
                    created_at=now_dt,
                    expires_at=now_dt + timedelta(days=settings.retention_audio_days),
                )
                db.add(audio_obj)
                await db.execute(
                    update(Job).where(Job.id == job_id).values(file_hash_sha256=downloaded_hash)
                )
                await db.commit()

        # 2. Probe audio properties
        probe_info = preprocessor.probe_audio(input_audio_path)
        total_duration = probe_info["duration"]
        max_duration_sec = settings.max_audio_duration_hours * 3600
        if total_duration > max_duration_sec:
            raise AppError(
                error="duration_exceeded",
                message=f"Длительность аудио ({total_duration:.1f} с) превышает лимит ({max_duration_sec} с)",
                status_code=400,
            )
        is_stereo = probe_info["channels"] >= 2

        # Check explicit channel mapping or stereo mode in request
        explicit_stereo = bool(
            data.get("diarization_mode") == "stereo"
            or data.get("channel_mapping")
            or data.get("channel_manager") is not None
            or (data.get("metadata") or {}).get("channel_mapping")
            or (data.get("options") or {}).get("diarization_mode") == "stereo"
        )

        if is_stereo and not explicit_stereo:
            # Check whether channels are acoustically isolated (telephony) or shared (dual-mono / mic bleed)
            is_isolated = preprocessor.check_channel_isolation(input_audio_path)
            if not is_isolated:
                logger.info(
                    "stereo_channels_not_isolated_using_mono_diarization",
                    call_id=call_id,
                    channels=probe_info["channels"],
                )
                is_stereo = False

        call_created_at_dt = None
        raw_call_created = data.get("metadata", {}).get("call_created_at")
        if raw_call_created:
            try:
                call_created_at_dt = datetime.fromisoformat(
                    str(raw_call_created).replace("Z", "+00:00")
                ).replace(tzinfo=None)
            except Exception:
                pass

        async with AsyncSessionLocal() as db:
            call_update_vals = {"duration_sec": total_duration}
            if call_created_at_dt:
                call_update_vals["call_created_at"] = call_created_at_dt
            await db.execute(update(Call).where(Call.id == call_id).values(**call_update_vals))
            await db.execute(
                update(Job).where(Job.id == job_id).values(progress=25, step="asr_transcription")
            )
            await db.commit()

        # 3. Transcription & Diarization
        final_segments = []
        is_answering_machine = False

        voc_hints = data.get("vocabulary_hints") or (data.get("options") or {}).get(
            "vocabulary_hints"
        )
        hints_prompt = None
        if voc_hints:
            if isinstance(voc_hints, list):
                hints_prompt = " ".join(str(h) for h in voc_hints if h)
            elif isinstance(voc_hints, str):
                hints_prompt = voc_hints

        from app.workers.chunking import chunk_audio, merge_and_deduplicate

        if is_stereo:
            # Stereo branch: Split channels
            ch0_wav, ch1_wav = preprocessor.split_stereo_channels(input_audio_path)
            ch0_pcm = os.path.join(tmp_dir, "ch0_pcm.wav")
            ch1_pcm = os.path.join(tmp_dir, "ch1_pcm.wav")
            target_lang = (
                data.get("language") or (data.get("options") or {}).get("language") or "ru"
            )
            preprocessor.convert_to_pcm16(ch0_wav, ch0_pcm)
            preprocessor.convert_to_pcm16(ch1_wav, ch1_pcm)

            ch0_chunks = chunk_audio(ch0_pcm, chunk_length_sec=600, overlap_sec=15)
            ch1_chunks = chunk_audio(ch1_pcm, chunk_length_sec=600, overlap_sec=15)
            total_chunks = len(ch0_chunks) + len(ch1_chunks)

            all_segs_ch0 = []
            for idx, chunk in enumerate(ch0_chunks):
                segs, _ = asr_engine.transcribe(
                    chunk["data"],
                    language=target_lang,
                    initial_prompt=hints_prompt,
                    hotwords=hints_prompt,
                )
                all_segs_ch0.append(segs)
                chunk_progress = 25 + int(30 * (idx + 1) / max(1, total_chunks))
                async with AsyncSessionLocal() as db:
                    await db.execute(
                        update(Job)
                        .where(Job.id == job_id)
                        .values(
                            progress=chunk_progress,
                            step=f"asr_transcription_ch0_chunk_{idx + 1}_of_{len(ch0_chunks)}",
                        )
                    )
                    await db.commit()
            segs_ch0 = merge_and_deduplicate(all_segs_ch0, ch0_chunks)

            all_segs_ch1 = []
            for idx, chunk in enumerate(ch1_chunks):
                segs, _ = asr_engine.transcribe(
                    chunk["data"],
                    language=target_lang,
                    initial_prompt=hints_prompt,
                    hotwords=hints_prompt,
                )
                all_segs_ch1.append(segs)
                chunk_progress = 25 + int(30 * (len(ch0_chunks) + idx + 1) / max(1, total_chunks))
                async with AsyncSessionLocal() as db:
                    await db.execute(
                        update(Job)
                        .where(Job.id == job_id)
                        .values(
                            progress=chunk_progress,
                            step=f"asr_transcription_ch1_chunk_{idx + 1}_of_{len(ch1_chunks)}",
                        )
                    )
                    await db.commit()
            segs_ch1 = merge_and_deduplicate(all_segs_ch1, ch1_chunks)

            channel_mapping = data.get("metadata", {}).get("channel_mapping")
            if not channel_mapping and data.get("channel_manager") is not None:
                try:
                    ch_mgr = int(data["channel_manager"])
                    channel_mapping = {
                        f"channel_{ch_mgr}": "manager",
                        f"channel_{1 - ch_mgr}": "client",
                    }
                except Exception:
                    channel_mapping = None

            final_segments, role_conf = role_classifier.assign_stereo_roles(
                segs_ch0, segs_ch1, channel_mapping
            )
        else:
            # Mono branch
            target_lang = (
                data.get("language") or (data.get("options") or {}).get("language") or "ru"
            )
            mono_pcm = os.path.join(tmp_dir, "mono_pcm.wav")
            preprocessor.convert_to_pcm16(input_audio_path, mono_pcm)

            chunks_info = chunk_audio(mono_pcm, chunk_length_sec=600, overlap_sec=15)
            all_raw_segments = []
            total_chunks = len(chunks_info)

            for idx, chunk in enumerate(chunks_info):
                segs, _ = asr_engine.transcribe(
                    chunk["data"],
                    language=target_lang,
                    initial_prompt=hints_prompt,
                    hotwords=hints_prompt,
                )
                all_raw_segments.append(segs)
                chunk_progress = 25 + int(30 * (idx + 1) / max(1, total_chunks))
                async with AsyncSessionLocal() as db:
                    await db.execute(
                        update(Job)
                        .where(Job.id == job_id)
                        .values(
                            progress=chunk_progress,
                            step=f"asr_transcription_chunk_{idx + 1}_of_{total_chunks}",
                        )
                    )
                    await db.commit()

            raw_segments = merge_and_deduplicate(all_raw_segments, chunks_info)

            async with AsyncSessionLocal() as db:
                await db.execute(
                    update(Job).where(Job.id == job_id).values(progress=55, step="mono_diarization")
                )
                await db.commit()

            # Use local audio-based diarization
            from app.diarization.mono_diarizer import MonoDiarizer

            mono_diarizer = MonoDiarizer(num_speakers=2)
            raw_segments = mono_diarizer.diarize(mono_pcm, raw_segments)

            final_segments, role_conf = role_classifier.classify_mono_speakers(raw_segments)

            # In local-full profile, semantically refine speaker roles with Qwen 2.5 7B if dialogue
            unique_speakers = {s.get("speaker_label") for s in final_segments}
            if settings.app_profile == "local-full" and final_segments and len(unique_speakers) > 1:
                try:
                    async with AsyncSessionLocal() as db:
                        await db.execute(
                            update(Job)
                            .where(Job.id == job_id)
                            .values(progress=58, step="llm_speaker_refinement")
                        )
                        await db.commit()
                    final_segments = await llm_adapter.refine_speaker_roles(final_segments)
                except Exception as e:
                    logger.warning("llm_speaker_refinement_failed_fallback", error=str(e))

        # 4. Check for Answering Machine in initial segments
        initial_text = " ".join(s["text"] for s in final_segments[:3])
        if vad_detector.detect_answering_machine(initial_text, total_duration):
            is_answering_machine = True
            logger.info("answering_machine_flagged", call_id=call_id)

        # 5. Calculate Deterministic Speech Metrics
        speech_metrics = metrics_calc.compute(final_segments, total_duration)

        async with AsyncSessionLocal() as db:
            await db.execute(
                update(Call)
                .where(Call.id == call_id)
                .values(is_answering_machine=is_answering_machine)
            )
            await db.execute(
                update(Job).where(Job.id == job_id).values(progress=60, step="pii_masking")
            )
            await db.commit()

        # 6. PII Masking & Segment Storage
        masked_full_lines = []
        async with AsyncSessionLocal() as db:
            for idx, s in enumerate(final_segments):
                raw_txt = s["text"]
                masked_txt = pii_masker.mask_text(raw_txt)
                masked_full_lines.append(f"[{s['speaker_role']}] {masked_txt}")

                seg_record = TranscriptSegment(
                    call_id=call_id,
                    namespace=namespace,
                    segment_order=idx + 1,
                    start_time=s["start"],
                    end_time=s["end"],
                    speaker_label=s["speaker_label"],
                    speaker_role=s["speaker_role"],
                    text_original=raw_txt,
                    text_masked=masked_txt,
                    confidence=s.get("confidence", 1.0),
                    words_json=s.get("words"),
                )
                db.add(seg_record)
            await db.commit()

        full_masked_text = "\n".join(masked_full_lines)

        # 7. Semantic Analysis (Skip LLM if Answering Machine is detected)
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(Job).where(Job.id == job_id).values(progress=80, step="semantic_analysis")
            )
            await db.commit()

        if is_answering_machine:
            analysis_result = {
                "schema_version": "1.0.0",
                "prompt_version": "v1.2-ru-outdoor",
                "summary": "Автоответчик или голосовая почта. Разговор не состоялся.",
                "call_result": "unclear_spam",
                "inquiry_type": "spam_irrelevant",
                "entities": {
                    "cities": [],
                    "formats": [],
                    "quantity": None,
                    "target_period": None,
                    "budget_rub": None,
                    "product_category": None,
                },
                "agreements": [],
                "sentiment": {"client_overall": "neutral", "manager_tone": "indifferent"},
                "manager_checklist": {
                    "greeting": False,
                    "need_identification": False,
                    "options_offered": False,
                    "next_step_named": False,
                    "contacts_taken": False,
                    "forbidden_words_found": [],
                    "score": 0,
                },
                "deterministic_speech_metrics": speech_metrics,
            }
        else:
            ctx = AnalysisContext(
                namespace=namespace,
                call_id=call_id,
                speech_duration_sec=total_duration,
            )
            structured_analysis = await llm_adapter.analyze(full_masked_text, ctx)
            analysis_result = structured_analysis.model_dump()
            analysis_result["deterministic_speech_metrics"] = speech_metrics

        # Strict JSON Schema validation & normalization
        try:
            from app.llm.local_adapter import normalize_analysis_dict

            analysis_result = normalize_analysis_dict(analysis_result, full_masked_text)
            validate_analysis_schema(analysis_result)
        except Exception as schema_err:
            logger.error("analysis_schema_validation_failed", error=str(schema_err))
            raise AppError(
                error="schema_validation_failed",
                message=f"Результат анализа не соответствует схеме: {schema_err}",
                status_code=500,
            )

        # 8. Save Analysis & Complete Job
        async with AsyncSessionLocal() as db:
            # Check if job was cancelled or call deleted
            j_stmt = select(Job).where(Job.id == job_id)
            current_job = (await db.execute(j_stmt)).scalar_one_or_none()
            if not current_job or (
                current_job.status == "failed" and current_job.step == "cancelled_by_deletion"
            ):
                logger.info("job_cancelled_skipping_save", job_id=job_id)
                return

            analysis_record = Analysis(
                call_id=call_id,
                namespace=namespace,
                schema_version=analysis_result["schema_version"],
                prompt_version=analysis_result["prompt_version"],
                summary=analysis_result["summary"],
                call_result=analysis_result["call_result"],
                inquiry_type=analysis_result["inquiry_type"],
                entities=analysis_result["entities"],
                agreements=analysis_result["agreements"],
                sentiment=analysis_result["sentiment"],
                manager_checklist=analysis_result["manager_checklist"],
                speech_metrics=speech_metrics,
            )
            db.add(analysis_record)

            await db.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(status="succeeded", progress=100, step="completed")
            )
            await db.commit()

        logger.info("job_completed_successfully", job_id=job_id, call_id=call_id)
        try:
            from app.metrics.prometheus import JOBS_TOTAL

            JOBS_TOTAL.labels(status="succeeded", namespace=namespace).inc()
        except Exception:
            pass

        # 9. Trigger callback if requested
        if callback_url:
            queue = get_queue()
            callback_payload = {
                "job_id": job_id,
                "call_id": call_id,
                "status": "succeeded",
                "duration_sec": total_duration,
                "is_answering_machine": is_answering_machine,
                "summary": analysis_result["summary"],
                "inquiry_type": analysis_result["inquiry_type"],
            }
            await queue.enqueue_callback(job_id, callback_url, callback_payload)

        # 10. Bitrix24 CRM timeline integration
        bitrix_meta = (data.get("metadata") or {}).get("bitrix24") or {}
        if bitrix_meta.get("webhook_url") and bitrix_meta.get("crm_entity_id"):
            try:
                # Use masked text for Bitrix24 timeline comment to prevent PII leakage
                comment_text = format_timeline_comment(
                    analysis=analysis_result,
                    full_text=full_masked_text,
                    duration_sec=total_duration,
                )
                await post_to_crm_timeline(
                    webhook_url=bitrix_meta["webhook_url"],
                    entity_type=bitrix_meta.get("crm_entity_type") or "deal",
                    entity_id=bitrix_meta["crm_entity_id"],
                    comment=comment_text,
                )
            except Exception as b24_err:
                logger.error("bitrix24_timeline_post_error", error=str(b24_err))


async def main():
    queue = get_queue()
    await queue.ensure_groups()
    logger.info("long_audio_processor_worker_started", group=GROUP_WORKERS)

    while is_running:
        try:
            job_item = await queue.read_next_job(
                stream=STREAM_LONG_JOBS,
                group=GROUP_WORKERS,
                consumer=f"worker_{os.getpid()}",
                block_ms=3000,
            )
            if not job_item:
                continue

            msg_id, job_id, payload = job_item
            try:
                await process_job(job_id, payload)
                await queue.ack_job(STREAM_LONG_JOBS, GROUP_WORKERS, msg_id)
            except Exception as e:
                is_permanent = isinstance(e, AppError)
                retry_count = int(payload.get("_retry_count", 0))
                max_retries = 3

                if not is_permanent and retry_count < max_retries:
                    retry_count += 1
                    payload["_retry_count"] = retry_count
                    logger.warning(
                        "job_processing_transient_error_retrying",
                        job_id=job_id,
                        retry_count=retry_count,
                        error=str(e),
                    )
                    async with AsyncSessionLocal() as db:
                        await db.execute(
                            update(Job)
                            .where(Job.id == job_id)
                            .values(
                                step=f"retrying_attempt_{retry_count}",
                                error_details={
                                    "error": "transient_error",
                                    "message": str(e),
                                    "retry": retry_count,
                                },
                            )
                        )
                        await db.commit()
                    # Re-enqueue for retry after delay
                    await asyncio.sleep(min(2**retry_count, 10))
                    await queue.enqueue_long_job(job_id, payload)
                    await queue.ack_job(STREAM_LONG_JOBS, GROUP_WORKERS, msg_id)
                    continue

                # Permanent failure or max retries exceeded
                error_type = e.error if isinstance(e, AppError) else "job_processing_error"
                error_msg = e.message if isinstance(e, AppError) else str(e)
                error_step = e.error if isinstance(e, AppError) else "failed"

                logger.error(
                    "job_processing_failed",
                    job_id=job_id,
                    error=error_msg,
                    error_type=error_type,
                    retries=retry_count,
                    traceback=traceback.format_exc(),
                )
                try:
                    from app.metrics.prometheus import JOBS_TOTAL

                    JOBS_TOTAL.labels(
                        status="failed", namespace=payload.get("namespace", "default")
                    ).inc()
                except Exception:
                    pass
                async with AsyncSessionLocal() as db:
                    await db.execute(
                        update(Job)
                        .where(Job.id == job_id)
                        .values(
                            status="failed",
                            step=error_step,
                            error_details={"error": error_type, "message": error_msg},
                        )
                    )
                    await db.commit()

                # Send failure callback if callback_url configured
                callback_url = payload.get("callback_url")
                if callback_url:
                    try:
                        failure_payload = {
                            "job_id": job_id,
                            "call_id": payload.get("call_id"),
                            "status": "failed",
                            "error": {"error": error_type, "message": error_msg},
                        }
                        await queue.enqueue_callback(job_id, callback_url, failure_payload)
                    except Exception as cb_err:
                        logger.error(
                            "failure_callback_enqueue_error", job_id=job_id, error=str(cb_err)
                        )

                # Move failed job to Dead Letter Queue (DLQ)
                try:
                    await queue.enqueue_dlq(
                        job_id=job_id,
                        error={"error": error_type, "message": error_msg},
                        payload=payload,
                    )
                except Exception as dlq_err:
                    logger.error("dlq_enqueue_failed", job_id=job_id, error=str(dlq_err))

                await queue.ack_job(STREAM_LONG_JOBS, GROUP_WORKERS, msg_id)

        except Exception as loop_err:
            logger.error("worker_loop_error", error=str(loop_err))
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
