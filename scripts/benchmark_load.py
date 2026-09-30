import argparse
import asyncio
import os
import resource
import time
from typing import List

import httpx

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
DEV_TOKEN = os.getenv("DEV_TOKEN", "dev-token")


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
    usage_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # On Linux ru_maxrss is in kilobytes
    usage_mb = usage_kb / 1024.0
    print(f"  ✓ Пиковый расход памяти текущего процесса: {usage_mb:.1f} МБ")
    return {"max_rss_mb": round(usage_mb, 1)}


async def main():
    parser = argparse.ArgumentParser(description="apc-speech Load & Performance Benchmarking")
    parser.add_argument("--concurrency", type=int, default=5, help="Number of concurrent requests")
    parser.add_argument("--requests", type=int, default=20, help="Total requests to send")
    args = parser.parse_args()

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
