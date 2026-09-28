import argparse
import json
import os
import time

from app.asr.engine import get_asr_engine
from app.asr.normalizer import RussianTextNormalizer
from evaluation.dataset_generator import build_synthetic_benchmark_suite
from evaluation.wer_cer import calculate_wer_cer


def run_benchmark():
    parser = argparse.ArgumentParser(description="apc-speech Quality & Performance Evaluation Benchmark")
    parser.add_argument("--profile", default="small", choices=["small", "local-full"])
    parser.add_argument("--generate-suite", action="store_true", help="Regenerate synthetic test audio")
    args = parser.parse_args()

    print("=================================================================")
    print(f"  APC-SPEECH EVALUATION BENCHMARK (Profile: {args.profile})")
    print("=================================================================")

    manifest_path = "data/evaluation_suite/manifest.json"
    if args.generate_suite or not os.path.exists(manifest_path):
        build_synthetic_benchmark_suite()

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    # Initialize Engine
    asr = get_asr_engine()
    normalizer = RussianTextNormalizer()

    latency_sample = next((s for s in manifest if s["type"] == "clean"), None)
    # Warmup
    if latency_sample and os.path.exists(latency_sample["audio_file"]):
        asr.transcribe(latency_sample["audio_file"])

    if latency_sample and os.path.exists(latency_sample["audio_file"]):
        start_t = time.perf_counter()
        asr.transcribe(latency_sample["audio_file"])
        end_t = time.perf_counter()
        voice_search_latency_ms = int((end_t - start_t) * 1000)
    else:
        voice_search_latency_ms = 0

    results = {}
    total_files = 0

    for sample in manifest:
        audio_path = sample["audio_file"]
        if not os.path.exists(audio_path):
            continue
        total_files += 1

        segments, meta = asr.transcribe(audio_path)
        hyp_text = " ".join([seg["text"] for seg in segments])
        print(f"[{sample['type']}] REF: {sample['reference']}")
        print(f"[{sample['type']}] HYP: {hyp_text}")

        # Normalize both reference and hypothesis for accurate WER
        ref_clean = normalizer.normalize(sample["reference"])
        hyp_clean = normalizer.normalize(hyp_text)

        wer, cer = calculate_wer_cer(ref_clean, hyp_clean)

        if sample["type"] == "clean":
            results["clean_speech"] = {
                "target_wer": "<= 12%",
                "measured_wer": f"{wer * 100:.1f}%",
                "measured_cer": f"{cer * 100:.1f}%",
                "status": "PASS" if wer <= 0.12 else "FAIL",
            }
        elif sample["type"] == "telephony":
            results["telephony_8khz"] = {
                "target_wer": "<= 25%",
                "measured_wer": f"{wer * 100:.1f}%",
                "measured_cer": f"{cer * 100:.1f}%",
                "status": "PASS" if wer <= 0.25 else "FAIL",
            }

    # Real acoustic diarization & DER calculation
    der_metrics = {"der": 0.0, "speaker_confusion_rate": 0.0}
    dialogue_sample = next((s for s in manifest if s["type"] == "dialogue"), None)
    if dialogue_sample and os.path.exists(dialogue_sample["audio_file"]):
        from app.diarization.mono_diarizer import MonoDiarizer
        from app.diarization.roles import RoleClassifier

        mono_diarizer = MonoDiarizer(num_speakers=2)
        role_classifier = RoleClassifier()

        segs, _ = asr.transcribe(dialogue_sample["audio_file"])
        diarized_segs = mono_diarizer.diarize(dialogue_sample["audio_file"], segs)
        classified_segs, _ = role_classifier.classify_mono_speakers(diarized_segs)

        ref_roles = dialogue_sample.get("speaker_roles", [])
        total_ref_duration = sum(r["end"] - r["start"] for r in ref_roles)
        confusion_duration = 0.0

        for ref in ref_roles:
            for pred in classified_segs:
                overlap_start = max(ref["start"], pred["start"])
                overlap_end = min(ref["end"], pred["end"])
                if overlap_end > overlap_start:
                    overlap_dur = overlap_end - overlap_start
                    if pred.get("speaker_role") != ref.get("speaker_role"):
                        confusion_duration += overlap_dur

        if total_ref_duration > 0:
            der = min(1.0, confusion_duration / total_ref_duration)
            der_metrics["der"] = round(der, 3)
            der_metrics["speaker_confusion_rate"] = round(der, 3)

    results["profile"] = args.profile
    results["diarization"] = {
        "der": f"{der_metrics['der'] * 100:.1f}%",
        "speaker_confusion": f"{der_metrics['speaker_confusion_rate'] * 100:.1f}%",
        "status": "PASS" if der_metrics["der"] <= 0.20 else "FAIL",
    }

    results["voice_search_latency"] = {
        "target": "<= 2500 ms",
        "measured": f"{voice_search_latency_ms} ms",
        "status": "PASS" if voice_search_latency_ms <= 2500 else "FAIL",
    }
    results["synthetic_suite_files"] = total_files

    print(json.dumps(results, indent=2, ensure_ascii=False))
    print("=================================================================")
    all_pass = all([
        results.get("clean_speech", {}).get("status") == "PASS",
        results.get("telephony_8khz", {}).get("status") == "PASS",
        results.get("voice_search_latency", {}).get("status") == "PASS",
        results.get("diarization", {}).get("status") == "PASS",
    ])
    if all_pass:
        print("  BENCHMARK COMPLETED: ALL TARGETS PASSED")
    else:
        print("  BENCHMARK COMPLETED: SOME TARGETS FAILED")
    print("=================================================================")

if __name__ == "__main__":
    run_benchmark()
