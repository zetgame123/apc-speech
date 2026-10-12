"""Прогрев ASR-моделей при старте воркеров (п.1 B1-догонки).

Холостой прогон на короткой синтетике (тон 440 Гц, 1 с, 16 кГц моно) греет
CTranslate2-ядра и VAD до первого реального задания. Воркер выставляет
Redis-флаг готовности (`worker:ready:{long,fast}` с TTL); /ready отдаёт 200
только при живых флагах.
"""

import asyncio
import io
import math
import struct
import wave

from app.core.logging import get_logger
from app.queue.redis_streams import RedisQueue

WORKER_READY_TTL_SEC = 90


def synthetic_warmup_wav(duration_sec: float = 1.0, sample_rate: int = 16000) -> bytes:
    """Генерирует короткий WAV с тоном (не тишина, чтобы VAD его не выкинул)."""
    n = int(sample_rate * duration_sec)
    frames = b"".join(
        struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / sample_rate)))
        for i in range(n)
    )
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(frames)
    return buf.getvalue()


async def mark_worker_ready(queue, name: str) -> None:
    """Выставить/обновить флаг готовности воркера."""
    await queue.set_worker_ready(name, ttl_sec=WORKER_READY_TTL_SEC)


async def heartbeat_loop(queue, name: str, interval_sec: int = 30) -> None:
    """Фоновая задача heartbeat готовности (п.1: не зависит от обработки заданий).

    Долгая транскрибация блокирует основной цикл воркера дольше TTL флага
    (90 с) — без отдельной задачи /ready падал бы в 503 на живых воркерах.
    Отмена — через task.cancel().
    """
    logger = get_logger("worker_heartbeat")
    while True:
        try:
            await mark_worker_ready(queue, name)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("worker_ready_heartbeat_failed", worker=name, error=str(e))
        try:
            await asyncio.sleep(interval_sec)
        except asyncio.CancelledError:
            raise


def start_heartbeat_thread(name: str, interval_sec: int = 30):
    """Фоновый поток heartbeat (п.1): не зависит от event loop воркера.

    Длинный синхронный инференс блокирует цикл на минуты — asyncio-задача
    в том же цикле голодает и флаг TTL протухает. Поток со своим циклом
    и собственным Redis-клиентом шлёт heartbeat дальше. Daemon: умирает
    вместе с процессом (остановка воркера -> 503 по истечении TTL).
    """
    import threading

    def _run() -> None:
        asyncio.run(_thread_beat(name, interval_sec))

    async def _thread_beat(worker_name: str, interval: int) -> None:
        queue = RedisQueue()
        await heartbeat_loop(queue, worker_name, interval)

    thread = threading.Thread(target=_run, name=f"heartbeat-{name}", daemon=True)
    thread.start()
    return thread
