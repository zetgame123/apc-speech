import asyncio
import os
import signal
import tempfile
import traceback

import httpx
from sqlalchemy import update

from app.asr.engine import get_asr_engine
from app.asr.preprocessor import AudioPreprocessor
from app.asr.vad import VadDetector
from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.core.security import validate_ssrf_url
from app.db.models import Analysis, Call, Job, TranscriptSegment
from app.db.session import AsyncSessionLocal
from app.diarization.roles import RoleClassifier
from app.llm.local_adapter import LocalLlamaCppAdapter
from app.llm.port import AnalysisContext
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
            source_url = validate_ssrf_url(data["source_url"])
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.get(source_url)
                resp.raise_for_status()
                with open(input_audio_path, "wb") as f:
                    f.write(resp.content)

        # 2. Probe audio properties
        probe_info = preprocessor.probe_audio(input_audio_path)
        total_duration = probe_info["duration"]
        is_stereo = probe_info["channels"] >= 2

        async with AsyncSessionLocal() as db:
            await db.execute(
                update(Call).where(Call.id == call_id).values(duration_sec=total_duration)
            )
            await db.execute(
                update(Job).where(Job.id == job_id).values(progress=25, step="asr_transcription")
            )
            await db.commit()

        # 3. Transcription & Diarization
        final_segments = []
        is_answering_machine = False

        if is_stereo:
            # Stereo branch: Split channels
            ch0_wav, ch1_wav = preprocessor.split_stereo_channels(input_audio_path)
            ch0_pcm = os.path.join(tmp_dir, "ch0_pcm.wav")
            ch1_pcm = os.path.join(tmp_dir, "ch1_pcm.wav")
            preprocessor.convert_to_pcm16(ch0_wav, ch0_pcm)
            preprocessor.convert_to_pcm16(ch1_wav, ch1_pcm)

            segs_ch0, _ = asr_engine.transcribe(ch0_pcm)
            segs_ch1, _ = asr_engine.transcribe(ch1_pcm)

            channel_mapping = data.get("metadata", {}).get("channel_mapping")
            final_segments, role_conf = role_classifier.assign_stereo_roles(
                segs_ch0, segs_ch1, channel_mapping
            )
        else:
            # Mono branch
            mono_pcm = os.path.join(tmp_dir, "mono_pcm.wav")
            preprocessor.convert_to_pcm16(input_audio_path, mono_pcm)
            raw_segments, _ = asr_engine.transcribe(mono_pcm)

            # Assign dummy speaker labels for role heuristic
            for i, s in enumerate(raw_segments):
                s["speaker_label"] = "SPEAKER_0" if i % 2 == 0 else "SPEAKER_1"

            final_segments, role_conf = role_classifier.classify_mono_speakers(raw_segments)

        # 4. Check for Answering Machine in initial segments
        initial_text = " ".join(s["text"] for s in final_segments[:3])
        if vad_detector.detect_answering_machine(initial_text, total_duration):
            is_answering_machine = True
            logger.info("answering_machine_flagged", call_id=call_id)

        # 5. Calculate Deterministic Speech Metrics
        speech_metrics = metrics_calc.compute(final_segments, total_duration)

        async with AsyncSessionLocal() as db:
            await db.execute(
                update(Call).where(Call.id == call_id).values(is_answering_machine=is_answering_machine)
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
                "entities": {"cities": [], "formats": []},
                "agreements": [],
                "sentiment": {"client_overall": "neutral", "manager_tone": "neutral"},
                "manager_checklist": {"score": 0, "greeting": False},
            }
        else:
            ctx = AnalysisContext(
                namespace=namespace,
                call_id=call_id,
                speech_duration_sec=total_duration,
            )
            structured_analysis = await llm_adapter.analyze(full_masked_text, ctx)
            analysis_result = structured_analysis.model_dump()

        # 8. Save Analysis & Complete Job
        async with AsyncSessionLocal() as db:
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
                logger.error("job_processing_failed", job_id=job_id, error=str(e), traceback=traceback.format_exc())
                async with AsyncSessionLocal() as db:
                    await db.execute(
                        update(Job)
                        .where(Job.id == job_id)
                        .values(
                            status="failed",
                            step="failed",
                            error_details={"error": "job_processing_error", "message": str(e)},
                        )
                    )
                    await db.commit()
                await queue.ack_job(STREAM_LONG_JOBS, GROUP_WORKERS, msg_id)

        except Exception as loop_err:
            logger.error("worker_loop_error", error=str(loop_err))
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
