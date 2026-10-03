#!/usr/bin/env python3
"""
Utility script to ensure required models are downloaded for local offline execution.
Supports downloading CAM++, Whisper models, and Qwen LLM weights.
"""

import argparse
import os
import sys
import urllib.request

MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models")

CAMPPLUS_URL = "https://modelscope.cn/models/iic/speech_campplus_sv_zh-cn_16k-common/resolve/master/campplus.onnx"
CAMPPLUS_PATH = os.path.join(MODELS_DIR, "campplus.onnx")

QWEN_BASE_URL = "https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/main"
QWEN_SHARDS = [
    "qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf",
    "qwen2.5-7b-instruct-q4_k_m-00002-of-00002.gguf",
]
QWEN_GGUF_URL = f"{QWEN_BASE_URL}/{QWEN_SHARDS[0]}"
QWEN_GGUF_PATH = os.path.join(MODELS_DIR, "qwen2.5-7b-instruct-q4_k_m.gguf")


def _reporthook(block_num, block_size, total_size):
    downloaded = block_num * block_size
    if total_size > 0:
        percent = min(100, downloaded * 100 // total_size)
        sys.stdout.write(
            f"\rProgress: {percent}% ({downloaded // (1024 * 1024)}MB / {total_size // (1024 * 1024)}MB)"
        )
        sys.stdout.flush()


def download_campplus():
    os.makedirs(MODELS_DIR, exist_ok=True)
    if os.path.exists(CAMPPLUS_PATH) and os.path.getsize(CAMPPLUS_PATH) > 1_000_000:
        print(
            f"[OK] CAM++ model already present at {CAMPPLUS_PATH} ({os.path.getsize(CAMPPLUS_PATH)} bytes)"
        )
        return

    print(f"Downloading CAM++ neural speaker embedding model to {CAMPPLUS_PATH}...")
    try:
        urllib.request.urlretrieve(CAMPPLUS_URL, CAMPPLUS_PATH, reporthook=_reporthook)
        print(f"\n[OK] Downloaded CAM++ successfully ({os.path.getsize(CAMPPLUS_PATH)} bytes)")
    except Exception as e:
        print(f"\n[ERROR] Failed to download CAM++: {e}")
        raise RuntimeError(f"Failed to download CAM++: {e}") from e


def download_whisper(model_size: str = "base"):
    os.makedirs(MODELS_DIR, exist_ok=True)
    output_dir = os.path.join(MODELS_DIR, f"whisper-{model_size}")
    if os.path.exists(output_dir) and any(os.scandir(output_dir)):
        print(f"[OK] Whisper {model_size} model already present at {output_dir}")
        return

    print(f"Downloading faster-whisper '{model_size}' model to {output_dir}...")
    try:
        from faster_whisper import download_model

        download_model(model_size, output_dir=output_dir)
        print(f"[OK] Downloaded Whisper {model_size} successfully")
    except Exception as e:
        print(f"[ERROR] Failed to download Whisper {model_size} via faster-whisper: {e}")
        raise RuntimeError(f"Failed to download Whisper {model_size}: {e}") from e


def download_qwen():
    os.makedirs(MODELS_DIR, exist_ok=True)
    if os.path.exists(QWEN_GGUF_PATH) and os.path.getsize(QWEN_GGUF_PATH) > 100_000_000:
        print(
            f"[OK] Qwen GGUF model already present at {QWEN_GGUF_PATH} ({os.path.getsize(QWEN_GGUF_PATH)} bytes)"
        )
        return

    # Check if shards are already downloaded in models dir
    shard1_path = os.path.join(MODELS_DIR, QWEN_SHARDS[0])
    shard2_path = os.path.join(MODELS_DIR, QWEN_SHARDS[1])
    if (
        os.path.exists(shard1_path)
        and os.path.getsize(shard1_path) > 100_000_000
        and os.path.exists(shard2_path)
        and os.path.getsize(shard2_path) > 100_000_000
    ):
        print(f"[OK] Qwen GGUF shards already present in {MODELS_DIR}")
        return

    print(f"Downloading Qwen2.5-7B-Instruct GGUF (~4.4 GB) to {QWEN_GGUF_PATH}...")
    try:
        urllib.request.urlretrieve(QWEN_GGUF_URL, QWEN_GGUF_PATH, reporthook=_reporthook)
        print(f"\n[OK] Downloaded Qwen GGUF successfully ({os.path.getsize(QWEN_GGUF_PATH)} bytes)")
    except Exception as e:
        print(f"\n[ERROR] Failed to download Qwen GGUF: {e}")
        raise RuntimeError(f"Failed to download Qwen GGUF: {e}") from e


def main():
    parser = argparse.ArgumentParser(description="Download required ML models for APC Speech.")
    parser.add_argument(
        "--profile",
        choices=["small", "local-full", "all"],
        default="small",
        help="Profile to download models for (default: small).",
    )
    args = parser.parse_args()

    print(f"=== APC Speech Model Downloader (profile: {args.profile}) ===")
    try:
        download_campplus()
        download_whisper("small")

        if args.profile in ("local-full", "all"):
            download_whisper("medium")
            download_qwen()
    except Exception as e:
        print(f"\n[FATAL] Model download failed: {e}", file=sys.stderr)
        sys.exit(1)

    print("=== All requested models verified ===")


if __name__ == "__main__":
    main()
