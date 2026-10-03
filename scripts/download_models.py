#!/usr/bin/env python3
"""
Utility script to ensure required models are downloaded for local offline execution.
Supports downloading CAM++, Whisper models, and Qwen LLM weights.
"""

import argparse
import os
import sys
import urllib.request

_opener = urllib.request.build_opener()
_opener.addheaders = [("User-Agent", "Mozilla/5.0 (compatible; APC-Speech/1.0)")]
urllib.request.install_opener(_opener)

MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models")

CAMPPLUS_URLS = [
    "https://huggingface.co/welcomyou/campplus-3dspeaker-200k-onnx/resolve/main/campplus_cn_en_common_200k.onnx",
    "https://huggingface.co/hb123xyz/campplus_cn_en_common_200k/resolve/main/campplus_cn_en_common_200k.onnx",
]
CAMPPLUS_PATH = os.path.join(MODELS_DIR, "campplus.onnx")

QWEN_BASE_URL = "https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/main"
QWEN_SHARDS = [
    "qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf",
    "qwen2.5-7b-instruct-q4_k_m-00002-of-00002.gguf",
]
QWEN_SHARD_URLS = [f"{QWEN_BASE_URL}/{name}" for name in QWEN_SHARDS]
QWEN_SHARD_PATHS = [os.path.join(MODELS_DIR, name) for name in QWEN_SHARDS]
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
    last_err = None
    for url in CAMPPLUS_URLS:
        try:
            urllib.request.urlretrieve(url, CAMPPLUS_PATH, reporthook=_reporthook)
            if os.path.exists(CAMPPLUS_PATH) and os.path.getsize(CAMPPLUS_PATH) > 1_000_000:
                print(
                    f"\n[OK] Downloaded CAM++ successfully ({os.path.getsize(CAMPPLUS_PATH)} bytes)"
                )
                return
        except Exception as e:
            last_err = e
            try:
                if os.path.exists(CAMPPLUS_PATH):
                    os.remove(CAMPPLUS_PATH)
            except OSError:
                pass
            print(f"\n[WARN] Failed to download CAM++ from {url}: {e}")

    print(f"\n[ERROR] Failed to download CAM++ from all sources: {last_err}")
    raise RuntimeError(f"Failed to download CAM++: {last_err}") from last_err


def download_whisper(model_name: str = "small", output_name: str | None = None):
    os.makedirs(MODELS_DIR, exist_ok=True)
    folder_name = output_name or (
        f"whisper-{model_name}" if "/" not in model_name else model_name.split("/")[-1]
    )
    output_dir = os.path.join(MODELS_DIR, folder_name)
    if os.path.exists(output_dir) and any(os.scandir(output_dir)):
        print(f"[OK] Whisper model ({model_name}) already present at {output_dir}")
        return

    print(f"Downloading faster-whisper '{model_name}' model to {output_dir}...")
    try:
        from faster_whisper import download_model

        download_model(model_name, output_dir=output_dir)
        print(f"[OK] Downloaded Whisper {model_name} successfully")
        return
    except Exception as e:
        print(
            f"[INFO] faster-whisper unavailable or failed ({e}), falling back to direct Hugging Face download..."
        )

    repo_id = "Systran/faster-whisper-small" if model_name == "small" else model_name
    files = [
        "config.json",
        "model.bin",
        "tokenizer.json",
        "vocabulary.json",
        "vocabulary.txt",
        "preprocessor_config.json",
    ]
    os.makedirs(output_dir, exist_ok=True)
    for filename in files:
        url = f"https://huggingface.co/{repo_id}/resolve/main/{filename}"
        dest = os.path.join(output_dir, filename)
        try:
            urllib.request.urlretrieve(url, dest, reporthook=_reporthook)
            print(f"\n[OK] Downloaded {filename}")
        except Exception:
            if os.path.exists(dest):
                try:
                    os.remove(dest)
                except OSError:
                    pass

    model_bin = os.path.join(output_dir, "model.bin")
    if not os.path.exists(model_bin) or os.path.getsize(model_bin) < 10_000_000:
        raise RuntimeError(
            f"Failed to download Whisper model {model_name}: model.bin missing or invalid"
        )
    print(f"[OK] Downloaded Whisper {model_name} successfully via direct download")


def download_qwen():
    os.makedirs(MODELS_DIR, exist_ok=True)
    mono_path = QWEN_GGUF_PATH
    if os.path.exists(mono_path) and os.path.getsize(mono_path) > 100_000_000:
        print(f"[OK] Qwen GGUF model already present at {mono_path}")
        return

    if all(
        os.path.exists(path) and os.path.getsize(path) > 100_000_000 for path in QWEN_SHARD_PATHS
    ):
        print(f"[OK] Qwen GGUF shards already present in {MODELS_DIR}")
        return

    print("Downloading Qwen2.5-7B-Instruct GGUF shards (~4.4 GB total)...")
    for url, path in zip(QWEN_SHARD_URLS, QWEN_SHARD_PATHS, strict=True):
        if os.path.exists(path) and os.path.getsize(path) > 100_000_000:
            print(f"[OK] Qwen shard already present at {path}")
            continue
        try:
            urllib.request.urlretrieve(url, path, reporthook=_reporthook)
            if os.path.getsize(path) <= 100_000_000:
                raise RuntimeError(f"downloaded file is unexpectedly small: {path}")
            print(f"\n[OK] Downloaded Qwen shard {os.path.basename(path)}")
        except Exception as e:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
            print(f"\n[ERROR] Failed to download Qwen shard {path}: {e}")
            raise RuntimeError(f"Failed to download Qwen shard {path}: {e}") from e


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
        # 1. Voice search model (whisper-small)
        download_whisper("small")
        # 2. Russian-adapted model for long audio processing (whisper-podlodka-turbo-ct2)
        download_whisper(
            "bond005/whisper-podlodka-turbo-ct2",
            output_name="whisper-podlodka-turbo-ct2",
        )

        if args.profile in ("local-full", "all"):
            download_qwen()
    except Exception as e:
        print(f"\n[FATAL] Model download failed: {e}", file=sys.stderr)
        sys.exit(1)

    print("=== All requested models verified ===")


if __name__ == "__main__":
    main()
