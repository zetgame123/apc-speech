# Отчёт о замерах производительности и качества (Benchmark Report)

Настоящий отчёт подготовлен в соответствии с требованиями раздела 7 `common-requirements.md` и раздела «Качество и скорость» `brief.md`.

---

## 1. Условия проведения замеров (актуализация 2026-11-20)

* **Тестовый стенд**: Docker Desktop Linux VM на `AMD Ryzen 7 8845HS`, `AVX2: true`, без GPU.
* **Профили**:
  * `small`: суммарный лимит RAM `3 408 MiB ≤ 4 096 MiB` (`worker-fast` `1.0 CPU / 640M`, `worker-long` `0.8 CPU / 1280M`, `api` `0.3 CPU / 512M`, `cleanup-retention` `0.05 CPU / 160M`, `postgres` `0.15 CPU / 256M`, `minio` `0.1 CPU / 272M`, `redis` `0.05 CPU / 160M`, `worker-callback` `0.05 CPU / 128M`).
  * `local-full`: добавляет `llm-runtime` (`llama.cpp` server-b5350, `Qwen3-4B-Q4_K_M.gguf`, 6 потоков, лимит `6.0 CPU / 10 GiB`).
* **ASR-движок**: `GigaAM-v3 E2E CTC INT8 ONNX` (`onnx-asr`, `GIGAAM_THREADS=1`, MIT, ~215 МБ).
* **Диаризация**: `MonoDiarizer` на `CAM++` ONNX (`Apache 2.0`, 28 МБ) с модульным кэшем сессии и ленивым акустическим fallback.
* **Аудиоформаты**: чистая речь 16 kHz PCM (`sample_clean_01_clean.wav`, `voice10_monologue.wav`) и телефонный тракт 8 kHz (`sample_telephony_01_telephony_8k.wav`, `sample_dialogue_01_telephony_8k.wav`, а также синтетические склейки 70.2 с и 306.2 с).

---

## 2. Сводные результаты замеров на живом Docker-стеке

| Показатель | Измеренное значение | Ориентир из `brief.md` | Статус | Примечание |
| :--- | :--- | :--- | :--- | :--- |
| **Voice Search 4.4s (clean 16k)** | **443 мс** med (`10/10 200 OK`) | ≤ 2 500 мс | **PASS** | `worker-fast` 1.0 CPU, RTF `0.101` |
| **Voice Search 5.2s (telephony 8k)** | **479 мс** med / **530 мс** p95 (`10/10 200 OK`) | ≤ 2 500 мс | **PASS** | RTF `0.092` |
| **Voice Search 10.0s (`voice10_monologue`)** | **962 мс** med / **1 085 мс** p95 (`10/10 200 OK`) | ≤ 2 500 мс | **PASS** | 0% `503`; запас > 2× до SLA |
| **Voice Search 11.2s (dialogue 8k)** | **1 000 мс** med / **1 091 мс** p95 (`10/10 200 OK`) | ≤ 2 500 мс | **PASS** | RTF `0.089` |
| **Long-audio E2E 70.2s (telephony 8k)** | **11.88 с** (E2E RTF **0.169**) | RTF ≤ 1.0 | **PASS** | Полный пайплайн (upload + S3 + ASR + CAM++ + PII + анализ + БД) на `0.8 CPU` |
| **Long-audio E2E 306.2s (5.1 мин, 8k)** | **41.67 с** (E2E RTF **0.136**) | RTF ≤ 1.0 | **PASS** | Прогноз на 1 ч аудио: **~8.2 мин** на `0.8 CPU` |
| **LLM-разбор звонка (`local-full`, Qwen3-4B)** | **24.1–28.4 с** (с KV-кешем промпта; 1-й холодный — 45.2 с) | ≤ 30 с | **PASS** | `llama.cpp` 6 потоков CPU, 100% валидный JSON, `think_tag=False`, RAM `2.47 GiB` |
| **WER на чистой речи (`evaluation.run`)** | **0.0%** (синтетика) | ≤ 12% | **PASS** | Встроенная пунктуация и регистр |
| **WER на телефонии 8 kHz (`evaluation.run`)** | **0.0–11.1%** (после нормализатора) | ≤ 25% | **PASS** | GigaAM пишет `3×6`, `200 000₽` (ITN из коробки) |
| **RAM стека `small` под нагрузкой (8 конт.)** | **1.62 GiB** (`worker-long` 765 MiB, `worker-fast` 330 MiB) | ≤ 4 096 MiB | **PASS** | Все контейнеры ≤ 60% своих лимитов (`cleanup-retention` 48%) |
| **Суммарный лимит RAM `small` в compose** | **3 408 MiB** (`3.33 GiB`) | ≤ 4 096 MiB | **PASS** | Запас 688 MiB до потолка |

---

## 3. Прямой A/B на одном контейнере (`--cpus=1.0`, `threads=1`)

### 3.1. ASR: `GigaAM-v3 E2E CTC INT8` vs `faster-whisper small INT8`

| Файл (длит.) | GigaAM-v3 (`th=1`) | faster-whisper `small` (`th=1`) | Ускорение |
|---|---:|---:|---:|
| Clean 4.37s | **536 мс** (RTF `0.123`, WER 0.0%) | 3 405 мс (RTF `0.779`, WER 16.7%) | **6.4×** |
| Telephony 8k 5.18s | **581 мс** (RTF `0.112`) | 4 071 мс (RTF `0.785`) | **7.0×** |
| Monologue 10.00s | **1 008 мс** (RTF `0.101`, все 3 фразы) | 3 811 мс (RTF `0.381`, проглотил 2 из 3 фраз) | **3.8×** |
| Dialogue 8k 11.20s | **1 152 мс** (RTF `0.103`) | 4 269 мс (RTF `0.381`) | **3.7×** |
| **Long 70.20s (8k)** | **7.59 с** (RTF `0.108`) | **75.21 с** (RTF `1.071`) | **9.9×** |
| Пик RSS процесса | **498 MiB** | **1 170 MiB** (до 2 072 MiB при `th=2`) | **-57% RAM** |

### 3.2. Диаризация: старый `MonoDiarizer` (`master`) vs новый (`feat/gigaam-qwen3-speedup`)

| Диалог | OLD (cold / warm med) | NEW (cold / warm med) | Ускорение (warm) |
|---|---:|---:|---:|
| 11.2s (3 реплики) | 20 653 мс / **5 202 мс** | 1 126 мс / **576 мс** | **9.0×** |
| 70.2s (18 реплик) | 22 805 мс / **23 494 мс** | 3 217 мс / **2 994 мс** | **7.8×** |

---

## 4. Найденные и устранённые узкие места

1. **Thread contention в ONNX Runtime**: на контейнерах с лимитом `≤ 1.0 CPU` настройка `GIGAAM_THREADS=2` замедляла инференс на **27–39%** относительно `GIGAAM_THREADS=1`. Зафиксировано `GIGAAM_THREADS=1` для обоих воркеров.
2. **`503` на 10s голосовом поиске при `0.5 CPU`**: перераспределение CPU в `docker-compose.yml` (`worker-fast` `0.5 → 1.0 CPU`) снизило p95 10s-запроса с >3.1 с (`80% 503`) до **1.085 с (`0% 503`)**.
3. **Холодный старт первой джобы**: прогрев `CAM++` и `PiiMasker` перенесён в `worker-long.prewarm()` (выполняется за ~5 с до `/ready = 200`).
4. **`cleanup-retention` на грани OOM**: лимит поднят с `80M` (94% в простое) до `160M` (48%), за счёт снижения лимита `worker-long` (`2112M → 1280M`).

---

## 5. Воспроизведение замеров

```bash
# 1. Оценочный стенд качества (WER, CER, DER, Latency, JSON Schema)
uv run python -m evaluation.run

# 2. Нагрузочный тест голосового поиска
uv run python -m scripts.benchmark_load --concurrency 5 --requests 20
```
