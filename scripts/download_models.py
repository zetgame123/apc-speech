#!/usr/bin/env python3
"""
Utility script to ensure required models are downloaded for local offline execution.
Supports downloading CAM++, Whisper models, and Qwen LLM weights.
"""

import argparse
import os
import sys
import time
import urllib.error
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


def download_file(
    url: str,
    dest: str,
    expected_min_size: int = 0,
    max_retries: int = 5,
    chunk_size: int = 1024 * 1024,
    timeout: int = 60,
    optional: bool = False,
) -> bool:
    """Download a file with streaming chunks, resumption, retry, and validation."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    temp_dest = dest + ".tmp"

    headers = {"User-Agent": "Mozilla/5.0 (compatible; APC-Speech/1.0)"}
    token = os.environ.get("HF_TOKEN")
    if token and "huggingface.co" in url:
        headers["Authorization"] = f"Bearer {token}"

    for attempt in range(1, max_retries + 1):
        existing_bytes = os.path.getsize(temp_dest) if os.path.exists(temp_dest) else 0
        req_headers = dict(headers)
        if existing_bytes > 0:
            req_headers["Range"] = f"bytes={existing_bytes}-"

        req = urllib.request.Request(url, headers=req_headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = resp.status
                content_range = resp.headers.get("Content-Range")
                total_size = 0
                if content_range and "/" in content_range:
                    try:
                        total_size = int(content_range.split("/")[-1])
                    except ValueError:
                        pass
                if total_size == 0:
                    cl = resp.headers.get("Content-Length")
                    if cl:
                        total_size = int(cl) + existing_bytes if existing_bytes > 0 else int(cl)

                mode = "ab" if (existing_bytes > 0 and status == 206) else "wb"
                downloaded = existing_bytes if (mode == "ab") else 0

                with open(temp_dest, mode) as f:
                    while True:
                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0:
                            pct = min(100, downloaded * 100 // total_size)
                            sys.stdout.write(
                                f"\rProgress: {pct}% ({downloaded // (1024 * 1024)}MB / {total_size // (1024 * 1024)}MB)"
                            )
                            sys.stdout.flush()

                if total_size > 0 and downloaded < total_size:
                    raise urllib.error.URLError(
                        f"Incomplete download: {downloaded}/{total_size} bytes"
                    )
                if expected_min_size > 0 and downloaded < expected_min_size:
                    raise RuntimeError(
                        f"Downloaded file unexpectedly small: {downloaded} bytes < {expected_min_size}"
                    )

                os.replace(temp_dest, dest)
                sys.stdout.write("\n")
                print(f"[OK] Downloaded {os.path.basename(dest)} ({downloaded} bytes)")
                return True

        except urllib.error.HTTPError as e:
            if optional and e.code == 404:
                if os.path.exists(temp_dest):
                    try:
                        os.remove(temp_dest)
                    except OSError:
                        pass
                return False
            if e.code == 416:
                # Range Not Satisfiable: corrupt partial file, reset
                if os.path.exists(temp_dest):
                    try:
                        os.remove(temp_dest)
                    except OSError:
                        pass
                continue

            print(
                f"\n[WARN] HTTP {e.code} ({e.reason}) for {os.path.basename(dest)} on attempt {attempt}/{max_retries}"
            )
            if attempt < max_retries:
                time.sleep(2 * attempt)
            else:
                if optional:
                    return False
                raise

        except Exception as e:
            print(
                f"\n[WARN] Error downloading {os.path.basename(dest)} on attempt {attempt}/{max_retries}: {e}"
            )
            if attempt < max_retries:
                time.sleep(2 * attempt)
            else:
                if optional:
                    return False
                raise

    return False


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
            if download_file(url, CAMPPLUS_PATH, expected_min_size=1_000_000, optional=False):
                return
        except Exception as e:
            last_err = e
            print(f"[WARN] Failed to download CAM++ from {url}: {e}")

    print(f"\n[ERROR] Failed to download CAM++ from all sources: {last_err}")
    raise RuntimeError(f"Failed to download CAM++: {last_err}") from last_err


def download_whisper(model_name: str = "small", output_name: str | None = None):
    os.makedirs(MODELS_DIR, exist_ok=True)
    folder_name = output_name or (
        f"whisper-{model_name}" if "/" not in model_name else model_name.split("/")[-1]
    )
    output_dir = os.path.join(MODELS_DIR, folder_name)
    model_bin = os.path.join(output_dir, "model.bin")
    if os.path.exists(model_bin) and os.path.getsize(model_bin) > 10_000_000:
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
        ("config.json", True, 100),
        ("model.bin", True, 10_000_000),
        ("tokenizer.json", True, 10_000),
        ("vocabulary.json", False, 10_000),
        ("vocabulary.txt", False, 10_000),
        ("preprocessor_config.json", False, 50),
    ]
    os.makedirs(output_dir, exist_ok=True)
    for filename, required, min_size in files:
        dest = os.path.join(output_dir, filename)
        if os.path.exists(dest) and os.path.getsize(dest) >= min_size:
            print(f"[OK] {filename} already present")
            continue
        url = f"https://huggingface.co/{repo_id}/resolve/main/{filename}"
        download_file(url, dest, expected_min_size=min_size, optional=not required)

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
