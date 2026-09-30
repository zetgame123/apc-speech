import argparse
import json
import os
import sys
import time

from app.asr.engine import get_asr_engine, get_voice_search_engine
from app.asr.normalizer import RussianTextNormalizer
from evaluation.dataset_generator import build_synthetic_benchmark_suite
from evaluation.wer_cer import levenshtein_distance, normalize_eval_text


def run_benchmark():
    parser = argparse.ArgumentParser(
        description="apc-speech Quality & Performance Evaluation Benchmark"
    )
    parser.add_argument("--profile", default="small", choices=["small", "local-full"])
    parser.add_argument(
        "--generate-suite", action="store_true", help="Regenerate synthetic test audio"
    )
    args = parser.parse_args()

    print("=================================================================")
    print(f"  APC-SPEECH EVALUATION BENCHMARK (Profile: {args.profile})")
    print("=================================================================")

    manifest_path = "data/evaluation_suite/manifest.json"
    if args.generate_suite or not os.path.exists(manifest_path):
        build_synthetic_benchmark_suite()

    if not os.path.exists(manifest_path):
        print("ERROR: Evaluation manifest data/evaluation_suite/manifest.json not found!")
        sys.exit(1)

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    # Initialize Engines
    asr = get_asr_engine()
    vs_engine = get_voice_search_engine()
    normalizer = RussianTextNormalizer()

    latency_sample = next((s for s in manifest if s.get("type") == "clean"), None)
    results = {}

    if latency_sample and os.path.exists(latency_sample["audio_file"]):
        # Warmup voice search engine
        vs_engine.transcribe(latency_sample["audio_file"], beam_size=1, word_timestamps=False)
        start_t = time.perf_counter()
        vs_engine.transcribe(latency_sample["audio_file"], beam_size=1, word_timestamps=False)
        end_t = time.perf_counter()
        voice_search_latency_ms = int((end_t - start_t) * 1000)
        results["voice_search_latency"] = {
            "target": "<= 2500 ms",
            "measured": f"{voice_search_latency_ms} ms",
            "status": "PASS" if voice_search_latency_ms <= 2500 else "FAIL",
        }
    else:
        results["voice_search_latency"] = {
            "target": "<= 2500 ms",
            "measured": "N/A",
            "status": "FAIL",
            "error": "Latency audio sample not found",
        }

    total_files = 0
    stats = {
        "clean": {"ref_words": 0, "err_words": 0, "ref_chars": 0, "err_chars": 0, "samples": 0},
        "telephony": {"ref_words": 0, "err_words": 0, "ref_chars": 0, "err_chars": 0, "samples": 0},
    }

    for sample in manifest:
        audio_path = sample.get("audio_file")
        if not audio_path or not os.path.exists(audio_path):
            continue
        sample_type = sample.get("type")
        if sample_type not in stats:
            continue

        total_files += 1
        segments, meta = asr.transcribe(audio_path)
        hyp_text = " ".join([seg["text"] for seg in segments])
        print(f"[{sample_type}] REF: {sample['reference']}")
        print(f"[{sample_type}] HYP: {hyp_text}")

        # Normalize both reference and hypothesis for accurate corpus WER/CER
        ref_clean = normalizer.normalize(sample["reference"])
        hyp_clean = normalizer.normalize(hyp_text)
        ref_norm = normalize_eval_text(ref_clean)
        hyp_norm = normalize_eval_text(hyp_clean)

        ref_words = ref_norm.split()
        hyp_words = hyp_norm.split()

        d_words = levenshtein_distance(ref_words, hyp_words)
        d_chars = levenshtein_distance(list(ref_norm), list(hyp_norm))

        stats[sample_type]["ref_words"] += len(ref_words)
        stats[sample_type]["err_words"] += d_words
        stats[sample_type]["ref_chars"] += len(ref_norm)
        stats[sample_type]["err_chars"] += d_chars
        stats[sample_type]["samples"] += 1

    # Corpus-weighted evaluation results
    if stats["clean"]["samples"] > 0 and stats["clean"]["ref_words"] > 0:
        clean_wer = stats["clean"]["err_words"] / stats["clean"]["ref_words"]
        clean_cer = stats["clean"]["err_chars"] / stats["clean"]["ref_chars"]
        results["clean_speech"] = {
            "target_wer": "<= 12%",
            "measured_wer": f"{clean_wer * 100:.1f}%",
            "measured_cer": f"{clean_cer * 100:.1f}%",
            "samples_evaluated": stats["clean"]["samples"],
            "status": "PASS" if clean_wer <= 0.12 else "FAIL",
        }
    else:
        results["clean_speech"] = {
            "target_wer": "<= 12%",
            "measured_wer": "N/A",
            "status": "FAIL",
            "error": "No clean speech samples found or evaluated",
        }

    if stats["telephony"]["samples"] > 0 and stats["telephony"]["ref_words"] > 0:
        tel_wer = stats["telephony"]["err_words"] / stats["telephony"]["ref_words"]
        tel_cer = stats["telephony"]["err_chars"] / stats["telephony"]["ref_chars"]
        results["telephony_8khz"] = {
            "target_wer": "<= 25%",
            "measured_wer": f"{tel_wer * 100:.1f}%",
            "measured_cer": f"{tel_cer * 100:.1f}%",
            "samples_evaluated": stats["telephony"]["samples"],
            "status": "PASS" if tel_wer <= 0.25 else "FAIL",
        }
    else:
        results["telephony_8khz"] = {
            "target_wer": "<= 25%",
            "measured_wer": "N/A",
            "status": "FAIL",
            "error": "No telephony samples found or evaluated",
        }

    # Acoustic diarization & DER calculation
    dialogue_sample = next((s for s in manifest if s.get("type") == "dialogue"), None)
    if dialogue_sample and os.path.exists(dialogue_sample.get("audio_file", "")):
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

        der = min(1.0, confusion_duration / total_ref_duration) if total_ref_duration > 0 else 1.0
        results["diarization"] = {
            "der": f"{der * 100:.1f}%",
            "speaker_confusion": f"{der * 100:.1f}%",
            "target_der": "<= 20%",
            "status": "PASS" if der <= 0.20 else "FAIL",
        }
    else:
        results["diarization"] = {
            "der": "N/A",
            "target_der": "<= 20%",
            "status": "FAIL",
            "error": "Dialogue audio sample not found",
        }

    results["profile"] = args.profile
    results["synthetic_suite_files"] = total_files

    print(json.dumps(results, indent=2, ensure_ascii=False))
    print("=================================================================")
    all_pass = all(
        [
            results.get("clean_speech", {}).get("status") == "PASS",
            results.get("telephony_8khz", {}).get("status") == "PASS",
            results.get("voice_search_latency", {}).get("status") == "PASS",
            results.get("diarization", {}).get("status") == "PASS",
        ]
    )
    if all_pass:
        print("  BENCHMARK COMPLETED: ALL TARGETS PASSED")
    else:
        print("  BENCHMARK COMPLETED: SOME TARGETS FAILED")
        sys.exit(1)
    print("=================================================================")


if __name__ == "__main__":
    run_benchmark()
