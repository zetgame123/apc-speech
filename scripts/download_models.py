#!/usr/bin/env python3
"""
Utility script to ensure required models are downloaded for local offline execution.
"""
import os
import sys
import urllib.request

MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models")

CAMPPLUS_URL = "https://modelscope.cn/models/iic/speech_campplus_sv_zh-cn_16k-common/resolve/master/campplus.onnx"
CAMPPLUS_PATH = os.path.join(MODELS_DIR, "campplus.onnx")


def download_campplus():
    os.makedirs(MODELS_DIR, exist_ok=True)
    if os.path.exists(CAMPPLUS_PATH) and os.path.getsize(CAMPPLUS_PATH) > 1_000_000:
        print(f"[OK] CAM++ model already present at {CAMPPLUS_PATH} ({os.path.getsize(CAMPPLUS_PATH)} bytes)")
        return

    print(f"Downloading CAM++ neural speaker embedding model to {CAMPPLUS_PATH}...")
    try:
        def reporthook(block_num, block_size, total_size):
            downloaded = block_num * block_size
            if total_size > 0:
                percent = min(100, downloaded * 100 // total_size)
                sys.stdout.write(f"\rProgress: {percent}% ({downloaded // (1024*1024)}MB / {total_size // (1024*1024)}MB)")
                sys.stdout.flush()

        urllib.request.urlretrieve(CAMPPLUS_URL, CAMPPLUS_PATH, reporthook=reporthook)
        print(f"\n[OK] Downloaded CAM++ successfully ({os.path.getsize(CAMPPLUS_PATH)} bytes)")
    except Exception as e:
        print(f"\n[WARN] Failed to download CAM++: {e}. Fallback acoustic clustering will be used.")


if __name__ == "__main__":
    download_campplus()
