import argparse
import asyncio
import json
import os
import platform
import shutil
import statistics
import subprocess

try:
    import resource
except ImportError:
    resource = None
import time
from typing import List

import httpx

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
DEV_TOKEN = os.getenv("DEV_TOKEN", "dev-token")

# Матрица C4 (brief.md п.4): длительности x частоты дискретизации.
# Пейлоады режутся из data/evaluation_suite/voice10_monologue.wav (16 кГц, моно, 10 с).
MATRIX_CELLS = [
    ("3", 16000),
    ("3", 8000),
    ("10", 16000),
    ("10", 8000),
]
MATRIX_SOURCE_WAV = os.path.join("data", "evaluation_suite", "voice10_monologue.wav")


async def benchmark_voice_search(concurrency: int = 10, total_requests: int = 50):
    print(
        f"\n[1/3] Запуск нагрузочного теста Voice Search (Потоков: {concurrency}, Всего: {total_requests})..."
    )

    # Generate a lightweight dummy WAV payload (4 seconds)
    audio_path = "data/evaluation_suite/sample_clean_01_clean.wav"
    if not os.path.exists(audio_path):
        from evaluation.dataset_generator import build_synthetic_benchmark_suite

        build_synthetic_benchmark_suite()

    with open(audio_path, "rb") as f:
        audio_bytes = f.read()

    headers = {
        "Authorization": f"Bearer {DEV_TOKEN}",
        "Content-Type": "audio/wav",
    }

    latencies: List[float] = []
    success_count = 0
    failure_count = 0

    semaphore = asyncio.Semaphore(concurrency)

    async def send_single_query(client: httpx.AsyncClient):
        nonlocal success_count, failure_count
        async with semaphore:
            t0 = time.time()
            try:
                resp = await client.post(
                    f"{API_BASE_URL}/v1/voice/search?namespace=benchmark_load",
                    headers=headers,
                    content=audio_bytes,
                    timeout=10.0,
                )
                elapsed_ms = (time.time() - t0) * 1000
                if resp.status_code == 200:
                    latencies.append(elapsed_ms)
                    success_count += 1
                else:
                    failure_count += 1
            except Exception:
                failure_count += 1

    async with httpx.AsyncClient() as client:
        tasks = [send_single_query(client) for _ in range(total_requests)]
        await asyncio.gather(*tasks)

    if latencies:
        latencies.sort()
        p50 = latencies[int(len(latencies) * 0.50)]
        p95 = latencies[int(len(latencies) * 0.95)]
        p99 = latencies[int(len(latencies) * 0.99)]
        avg = sum(latencies) / len(latencies)
    else:
        p50 = p95 = p99 = avg = 0.0

    print(f"  ✓ Успешных запросов: {success_count} / {total_requests}")
    print(f"  ✓ Ошибок / таймаутов: {failure_count}")
    print(f"  ✓ Среднее время: {avg:.1f} мс")
    print(f"  ✓ P50: {p50:.1f} мс")
    print(f"  ✓ P95: {p95:.1f} мс (Целевой SLA <= 2500 мс)")
    print(f"  ✓ P99: {p99:.1f} мс")

    return {
        "success_rate": f"{(success_count / total_requests) * 100:.1f}%",
        "avg_ms": round(avg, 1),
        "p50_ms": round(p50, 1),
        "p95_ms": round(p95, 1),
        "p99_ms": round(p99, 1),
    }


def measure_memory_footprint():
    print("\n[2/3] Замер пикового потребления памяти процесса (RAM RSS)...")
    if resource is not None:
        usage_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # On Linux ru_maxrss is in kilobytes
        usage_mb = usage_kb / 1024.0
    else:
        usage_mb = 0.0
    print(f"  ✓ Пиковый расход памяти текущего процесса: {usage_mb:.1f} МБ")
    return {"max_rss_mb": round(usage_mb, 1)}


def _read_cpu_model() -> str:
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def _read_governor() -> str:
    try:
        with open("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor") as f:
            return f.read().strip()
    except OSError:
        return "unknown"


def ensure_matrix_payloads(out_dir: str) -> dict:
    """Нарезать 4 пейлоада (3/10 с x 8/16 кГц, PCM16 mono) из эталона 10 с/16 кГц.

    ffmpeg вызывается массивом аргументов (без shell); если бинаря нет —
    чистый python-fallback (срез кадров + децимация для 8 кГц).
    """
    import wave

    os.makedirs(out_dir, exist_ok=True)
    if not os.path.exists(MATRIX_SOURCE_WAV):
        raise FileNotFoundError(f"нет эталона {MATRIX_SOURCE_WAV}")

    payloads = {}
    specs = {
        "s3_16k": (3, 16000),
        "s3_8k": (3, 8000),
        "s10_16k": (10, 16000),
        "s10_8k": (10, 8000),
    }
    ffmpeg = shutil.which("ffmpeg")
    for name, (dur, rate) in specs.items():
        path = os.path.join(out_dir, f"{name}.wav")
        if ffmpeg:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-v",
                    "error",
                    "-i",
                    MATRIX_SOURCE_WAV,
                    "-t",
                    str(dur),
                    "-ar",
                    str(rate),
                    "-ac",
                    "1",
                    "-c:a",
                    "pcm_s16le",
                    path,
                ],
                check=True,
            )
        else:
            with wave.open(MATRIX_SOURCE_WAV, "rb") as src:
                assert src.getframerate() == 16000 and src.getnchannels() == 1
                frames = src.readframes(int(dur * 16000))
            if rate == 8000:
                import array

                samples = array.array("h", frames)[::2]
                frames = samples.tobytes()
            with wave.open(path, "wb") as dst:
                dst.setnchannels(1)
                dst.setsampwidth(2)
                dst.setframerate(rate)
                dst.writeframes(frames)
        payloads[name] = path
    return payloads


def benchmark_voice_matrix(repeats: int = 20, warmup: int = 1, out_path: str | None = None) -> dict:
    """Замер C4: последовательно по `repeats` запросов на ячейку после прогрева.

    Первый (прогревочный) запрос каждой ячейки фиксируется отдельно:
    холодный движок/кэш дают выброс, не смешиваем его с медианой.
    Запросы идут строго последовательно — меряем чистую латентность
    на 2 ядрах, а не конкуренцию.
    """
    print("=================================================================")
    print("  APC-SPEECH VOICE C4 MATRIX (brief.md п.4: 10 с -> текст <= 2.5 с)")
    print("=================================================================")

    payloads = ensure_matrix_payloads(os.path.join("tmp", "bench_wav"))
    headers = {
        "Authorization": f"Bearer {DEV_TOKEN}",
        "Content-Type": "audio/wav",
    }
    env_info = {
        "cpu_model": _read_cpu_model(),
        "governor": _read_governor(),
        "cpuset": os.getenv("BENCH_CPUSET", "4,6 (см. docker-compose.bench-small.yml)"),
        "api_base_url": API_BASE_URL,
        "target_sla_ms": 2500,
    }

    cells = {}
    with httpx.Client(timeout=30.0) as client:
        for dur_label, rate in MATRIX_CELLS:
            name = f"s{dur_label}_{rate // 1000}k"
            with open(payloads[name], "rb") as f:
                audio = f.read()
            print(f"\n[ячейка {name}] прогрев x{warmup}, замер x{repeats}...")

            first = None
            for _ in range(warmup):
                t0 = time.time()
                try:
                    resp = client.post(
                        f"{API_BASE_URL}/v1/voice/search?namespace=benchmark_c4",
                        headers=headers,
                        content=audio,
                    )
                    first = {
                        "status": resp.status_code,
                        "latency_ms": round((time.time() - t0) * 1000, 1),
                    }
                except Exception as exc:
                    first = {"status": "exception", "latency_ms": None, "error": str(exc)}

            latencies: List[float] = []
            statuses: List[int | str] = []
            n_503 = 0
            for _ in range(repeats):
                t0 = time.time()
                try:
                    resp = client.post(
                        f"{API_BASE_URL}/v1/voice/search?namespace=benchmark_c4",
                        headers=headers,
                        content=audio,
                    )
                    elapsed = (time.time() - t0) * 1000
                    statuses.append(resp.status_code)
                    if resp.status_code == 200:
                        latencies.append(elapsed)
                    if resp.status_code == 503:
                        n_503 += 1
                except Exception:
                    statuses.append("exception")

            lat_sorted = sorted(latencies)
            cell = {
                "payload": name,
                "wav_bytes": len(audio),
                "first_request": first,
                "n_total": repeats,
                "n_ok": len(latencies),
                "n_503": n_503,
                "share_503": round(n_503 / repeats, 3),
                "median_ms": round(statistics.median(lat_sorted), 1) if lat_sorted else None,
                "p95_ms": round(lat_sorted[max(0, int(len(lat_sorted) * 0.95) - 1)], 1)
                if lat_sorted
                else None,
            }
            cells[name] = cell
            print(
                f"  первый: {first} | ok {cell['n_ok']}/{repeats}, "
                f"медиана {cell['median_ms']} мс, p95 {cell['p95_ms']} мс, "
                f"доля 503: {cell['share_503']}"
            )

    report = {"env": env_info, "cells": cells}
    if out_path:
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\nОтчёт записан: {out_path}")
    return report


async def main():
    parser = argparse.ArgumentParser(description="apc-speech Load & Performance Benchmarking")
    parser.add_argument("--concurrency", type=int, default=5, help="Number of concurrent requests")
    parser.add_argument("--requests", type=int, default=20, help="Total requests to send")
    parser.add_argument(
        "--matrix",
        action="store_true",
        help="Замер C4: 3/10 с x 8/16 кГц, прогрев + N повторов последовательно",
    )
    parser.add_argument("--repeats", type=int, default=20, help="Повторов на ячейку в --matrix")
    parser.add_argument("--warmup", type=int, default=1, help="Прогревочных запросов в --matrix")
    parser.add_argument("--out", type=str, default="", help="Путь к JSON-отчёту --matrix")
    args = parser.parse_args()

    if args.matrix:
        benchmark_voice_matrix(repeats=args.repeats, warmup=args.warmup, out_path=args.out or None)
        return

    print("=================================================================")
    print("  APC-SPEECH LOAD & PERFORMANCE BENCHMARK")
    print("=================================================================")

    # Test if API is alive first
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{API_BASE_URL}/health")
            if resp.status_code != 200:
                print(f"Внимание: сервер {API_BASE_URL} вернул статус {resp.status_code}")
    except Exception as e:
        print(f"Сервер {API_BASE_URL} недоступен локально ({e}). Запускаем оффлайн замер памяти...")

    vs_results = None
    try:
        vs_results = await benchmark_voice_search(args.concurrency, args.requests)
    except Exception as e:
        print(f"Пропуск онлайн замера (сервер не запущен): {e}")

    mem_results = measure_memory_footprint()

    print("\n[3/3] Сводная таблица результатов:")
    print("-----------------------------------------------------------------")
    print("| Показатель                      | Значение      | Цель SLA    |")
    print("-----------------------------------------------------------------")
    if vs_results:
        print(f"| Voice Search P50 Latency        | {vs_results['p50_ms']:>6} мс   | <= 2500 мс  |")
        print(f"| Voice Search P95 Latency        | {vs_results['p95_ms']:>6} мс   | <= 2500 мс  |")
        print(
            f"| Voice Search Успешность         | {vs_results['success_rate']:>7}       | >= 99%      |"
        )
    print(
        f"| RAM RSS (Process Peak)          | {mem_results['max_rss_mb']:>6} МБ   | <= 3500 МБ  |"
    )
    print("-----------------------------------------------------------------")
    print("  БЕНЧМАРК ЗАВЕРШЕН УСПЕШНО")
    print("=================================================================")


if __name__ == "__main__":
    asyncio.run(main())
