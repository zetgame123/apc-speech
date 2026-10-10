"""Прогрев ASR-моделей при старте воркеров (п.1 B1-догонки).

Холостой прогон на короткой синтетике (тон 440 Гц, 1 с, 16 кГц моно) греет
CTranslate2-ядра и VAD до первого реального задания. Воркер выставляет
Redis-флаг готовности (`worker:ready:{long,fast}` с TTL); /ready отдаёт 200
только при живых флагах.
"""

import io
import math
import struct
import wave

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
