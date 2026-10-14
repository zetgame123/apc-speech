# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Этап D (контракт и пагинация)**: примеры запросов/ответов для всех ручек
  `openapi.yaml` + контрактный тест на их наличие; курсорная пагинация
  (`cursor`/`next_cursor`, кап 100) в `GET /v1/voice/history` и
  `GET /v1/admin/tokens/revoked`; тест «Swagger /docs только вне production»;
  раздел README «Что делать, если...»; `.env.example` покрывает все переменные.
- **Audit Re-Check Resolutions (R-01 through R-13)**:
  - **R-01 (Strict Schema & Enum Canonicalization)**: Enforced strict enum typing (`Literal[...]`) across Pydantic models for `agreements.who`, `StructuredAnalysis.call_result`, and `sentiment` fields; implemented canonical slug normalization (`normalize_analysis_dict`) handling Russian synonyms and role variations; configured dynamic taxonomy validation against `config/taxonomy.yaml` with custom runtime adapter registration.
  - **R-02 (Robust Model Downloader & Multi-Shard Provisioning)**: Updated `scripts/download_models.py` to fetch official Qwen 2.5 7B GGUF split shards (`qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf` & `00002`) from Hugging Face, added strict size verification and exception raising on download failures, ensuring non-zero exit codes.
  - **R-03 (PII Sanitization in Validation Logs)**: Filtered sensitive `input` and `ctx` data from FastAPI `RequestValidationError.errors()` in `app/main.py` before logging, preventing email and phone number leakage into application logs.
  - **R-04 (Full Spoken Phone Digit Sequence Masking)**: Implemented `convert_spoken_phones()` tokenizer in `app/pii/masker.py` handling arbitrary spelled-out spoken Russian digit sequences (e.g. "восемь девять один два...", "плюс семь девятьсот двенадцать... ноль семь ноль девять") and converting them to standardized numbers for masking.
  - **R-05 (Context-Aware Accounting vs Billboard Normalization)**: Refined `app/asr/normalizer.py` with positive lookahead for outdoor advertising structures and parameters (dimensions, months, highways) when normalizing "счет" to "щит", protecting accounting contexts ("счет на электронную почту", "счет на имя клиента", "счет на сумму 25000 рублей").
  - **R-06 (Standardized HTTP 400 for Malformed JSON)**: Added dedicated handler for JSON decode / parse syntax errors (`json_invalid`) in `app/main.py` returning HTTP 400 Bad Request with standardized `{error, message, details}` envelope.
  - **R-07 (Production Startup Hardening for Callback Secrets)**: Enforced immediate startup abort (`RuntimeError`) when running with `APP_ENV=production` if `CALLBACK_SIGNING_SECRET` is unset, shorter than 32 characters, or contains default placeholders.
  - **R-08 (Documented Content-Hash vs Idempotency-Key Semantics)**: Clarified and aligned idempotency behavior in `app/api/jobs.py` and `openapi.yaml`: explicit `Idempotency-Key` guarantees strict preservation of previous job results, while implicit content-hash deduplication permits retry for failed jobs.
  - **R-09 (Aligned Voice Search Beam Size)**: Configured consistent `VOICE_SEARCH_BEAM_SIZE = 2` across `docker-compose.yml`, `app/api/voice.py`, and application documentation to ensure optimal recognition accuracy within latency SLAs.
  - **R-10 (Reliable Worker Process State Healthchecks)**: Enhanced worker container healthchecks in `docker-compose.yml` to inspect `/proc/*/status` for active running states (`R`, `S`, `D`), detecting stopped processes (`SIGSTOP`, state `T`) and zombie states (`Z`).
  - **R-11 (Custom Prometheus Metrics & Scheduled Retention)**: Implemented custom Prometheus metrics in `app/metrics/prometheus.py` (`apc_http_requests_total`, `apc_http_request_duration_seconds`, `apc_jobs_total`, `apc_voice_search_duration_seconds`, `apc_queue_size`), registered automatic metrics collection in API middleware, and added `cleanup-retention` cron service to `docker-compose.yml`.
  - **R-12 (Runtime and Static OpenAPI Spec Parity)**: Overrode FastAPI default schema generation in `app/main.py` with direct serving of canonical `openapi.yaml`, ensuring 100% parity of operational response codes (400, 401, 413, 429, 500).
  - **R-13 (Worker Memory Sizing for High-Throughput Voice Search)**: Increased `worker-fast` memory limit to 1024M in `docker-compose.yml` to prevent OOM terminations (exit code 137). Позже лимит снижен до 960M (текущее значение в `docker-compose.yml`).
- **Independent Technical Audit Resolutions (F-01 through F-15)**:
  - **F-01 / F-06 (Model Downloader & Provisioning)**: Enhanced `scripts/download_models.py` with multi-profile support (`--profile small|local-full|all`) for prefetching CAM++, Whisper, and Qwen GGUF weights.
  - **F-02 / F-10 (LLM Analysis Slugs & Schema Alignment)**: Fixed LLM system and user prompts to require standard English slugs for `inquiry_type` and `call_result`; added synonym mapping normalization; adjusted schema to support dynamic taxonomy categories without breaking schema validation.
  - **F-03 (Strict Idempotency Preservation)**: Guaranteed immutable idempotency for failed jobs when client passes explicit `Idempotency-Key`, returning the existing job record rather than rewriting the key.
  - **F-04 (Voice Search Latency Optimization)**: Optimized default `VOICE_SEARCH_BEAM_SIZE` to 1 and implemented an in-memory 16kHz mono WAV fast-path bypassing unnecessary ffmpeg subprocesses, reducing 10s voice latency from 2841 ms to 1482 ms on 2 CPU cores.
  - **F-05 (Enhanced PII Masking)**: Added dot-separated phone format support (`8.912.345.67.89`) and conversational Russian spoken phone number parsing and masking.
  - **F-07 (Unified Error Envelope)**: Registered global `RequestValidationError` and `StarletteHTTPException` handlers ensuring all validation and malformed JSON errors strictly adhere to the `{error, message, details}` contract.
  - **F-08 (Speech Normalization Refinement)**: Added ordinal calendar date conversions, spoken thousands number expansion (`25 000 руб.`), spoken phone normalization, and protected accounting terms (`счет на оплату`) from accidental outdoor homophone substitution (`щит на оплату`).
  - **F-09 (Zero-Vector Clustering Stability)**: Handled stop-word and empty text edge cases in `TopicClusteringEngine` by switching to Euclidean metric fallback in `AgglomerativeClustering`.
  - **F-11 (Environment Config Hardening)**: Sanitized `.env.example` by replacing default database and MinIO credentials with clear placeholders, and strengthened production startup verification against default secrets.
  - **F-12 (Worker Process Healthchecks)**: Replaced dummy healthchecks in `docker-compose.yml` with `/proc` process liveness inspections to monitor actual worker daemon state.
  - **F-13 (Documentation & Health Contract)**: Aligned `README.md` health endpoint output documentation with runtime response schema (`app_profile`, `llm_model`).
- **Neural Speaker Diarization (CAM++)**: Replaced heuristic MFCC/KMeans with ONNX CAM++ speaker embeddings, stereo isolation detection, anchor-based clustering, and HMM Viterbi smoothing.
- **LLM Speaker Role Refinement**: Integrated local `Qwen 2.5 7B Instruct` via `llama.cpp` server with strict JSON Schema constrained generation for manager/client role classification.
- **Voice Search & Zero Audio Retention**: Added `POST /v1/voice/search` for rapid in-memory speech recognition (SLA $\le 2.5$ s) with outdoor advertising vocabulary boosting and strict zero audio retention policy (no disk/DB/S3 storage).
- **Shared Server-Side Voice History**: Added `GET /v1/voice/history` and `DELETE /v1/voice/history` backed by Redis (`voice:history:{namespace}`) with PII masking and automatic TTL expiration (`RETENTION_AUDIO_DAYS`).
- **Synthetic Audio Generation**: Added `GET /v1/voice/sample-audio` for testing audio ingestion pipelines and healthchecks without external audio files.
- **Dead Letter Queue (DLQ)**: Implemented `STREAM_DLQ = "jobs:audio:dlq"` in Redis Streams with retry limits and exponential backoff for handling permanent job failures.
- **Safe Streaming Ingestion**: Multipart audio uploads now stream chunks directly to disk with hard byte caps to prevent memory exhaustion (anti-OOM).
- **S3 & Telephony Integrations**: Added Bitrix24 telephony connector, S3 presigned URL generation, and S3 inbox folder watcher daemon.
- **Topic Clustering**: Added offline query clustering using TF-IDF vectorization and HDBSCAN for outdoor advertising requests.
- **Interactive Speaker Timeline (Gantt Chart)**: Added visual speaker timeline above audio waveform in web dashboard with manager (blue) and client (orange) speech segments, hover tooltips, and click-to-seek audio playback.
- **Fine-Grained Emotion Breakdown**: Added speech emotion classification (`client_emotion`: enthusiasm/joy, neutral/business, doubt/skepticism, irritation/anger, urgency/anxiety; `manager_emotion`: enthusiastic/helpful, calm/professional, indifferent/bored, tense/defensive) with distribution percentages (`positive_pct`, `neutral_pct`, `negative_pct`) and visual progress bar.
- **Integral Compliance Score & Violations Engine**: Added 0–100% weighted script compliance scoring, automatic detection of script violations, stop-words, and tone penalties, with actionable supervisory coaching recommendations.
- **Direct 1-Click Transcript Downloads**: Added `.TXT`, `.SRT`, and `.JSON` quick-download buttons on the dashboard transcript view and standard `Content-Disposition: attachment` download headers on `GET /v1/calls/{id}/transcript`.
- **Test Suite Expansion**: Added comprehensive tests covering compliance calculation, emotions breakdown, and transcript downloads, increasing test count to 97 automated tests (`tests/test_compliance_and_emotions.py`).

### Changed
- **Голосовой поиск по умолчанию — T-one (ADR-015)**: `VOICE_SEARCH_ENGINE=tone`,
  `TONE_THREADS=1`, окно `VOICE_SEARCH_RESULT_TIMEOUT_SEC=2.5`; возврат на whisper —
  `VOICE_SEARCH_ENGINE=whisper`. KenLM не подключается (ADR-014).
- **Разбор Qwen детерминирован**: `temperature=0.0` + `seed=42`; повтор набора B
  3 раза — 0 расхождений. Пустые сущности Qwen добиваются правилами
  (`backfill_entities_from_rules`), `product_category` — по закрытому списку
  `config/product_categories.yaml`.
- **Потоки llama.cpp**: `LLAMA_THREADS` с дефолтом 4 (на 8 физических ядрах
  оптимум `-t 6`, медиана разбора 25,8 с — в норме ≤ 30 с).
- **Сущности правил**: экстракторы `extract_period` (относительные/сезоны/
  длительности + `canonical_period`), `extract_budget_rub`, `extract_category`;
  нормалайзер без склеек цифр, quantity словами + «штук», год — не бюджет.
- **Флак rate-limit**: управляемые часы в фикстуре вместо wall-clock.
- **`scripts/eval_wer.py`**: ленивая загрузка движков (только запрошенные
  в `--engines`) + сброс кэша гипотез каждые 10 файлов — чанки
  и продолжение после обрыва тем же `--hyps-in`.

### Measured (стенд: Ryzen 7 8845HS, скрипты репозитория, см. `docs/eval-report.md`)
- **WER Golos-100**: T-one 16,1 % / 19,6 % (clean/tel), podlodka 18,4 % / 35,2 %,
  whisper-small 38,2 % / 57,2 %; ориентир ≤ 12 % / ≤ 25 % — мимо по чистой,
  T-one OK по телефонии. Термины с подсказками 63,6 % против ≥ 90 % — мимо.
- **WER RuLS-30 / SOVA-26** (профиль small, длинный путь + T-one): см. `docs/eval-report.md` §15.
- **WER RuLS-296 / SOVA-265 (2026-10-14, кап 3,0 ГГц)**: RuLS — podlodka
  9,8/10,5 %, T-one 6,0/7,9 % (всё OK); SOVA — телефония OK,
  чистая речь мимо у обоих (13,7 % / 13,0 % против 12 %).
  Полные сплиты — не измерено (объём счёта). См. `docs/eval-report.md` §18.
- **ASR-шум с новыми эталонами (2026-10-14)**: типы −0,08 (rules) / −0,12
  (Qwen); сущности почти без потерь. См. `docs/eval-report.md` §17.
- **DER-30**: моно 0,163 / роли 0,94; стерео 0,286 / роли 0,98.
- **Типы/сущности A120+B25**: Qwen-B accuracy 0,80 / macro-F1 0,67 (промпт v1.5);
  rules-B 0,60 / 0,53; сущности v16 — см. отчёт §14.
- **Часовая запись small (2 ядра)**: 3431 с, RTF 0,953 — OK впритык;
  local-full 8 vCPU выигрыша не даёт (RTF 0,960).
- **Часовая запись small с капом 3,0 ГГц (2026-10-14)**: 5027,5 с,
  RTF 1,397 — мимо ≤ 1,0; пик RAM — не измерено (сэмплинг прерван,
  см. `docs/eval-report.md` §16).
- **Стоимость провайдера**: 802 + 235 ток./разбор расчётом; факт не измерен (нет ключа).

## [0.1.0] - 2026-10-13
- **Strict Schema Validation**: Analysis jobs fail with `schema_validation_failed` error when LLM output violates schema, preventing corrupted records in database.
- **Audio Duration Limit**: Enforced immediate rejection of audio files exceeding `MAX_AUDIO_DURATION_HOURS` right after audio probing.
- **Cross-Worker Token Revocation**: Made authentication verification asynchronous with real-time revoked token synchronization across all workers via Redis set `revoked_tokens`.
- **Scoped Voice History Access**: Added `read` scope enforcement for `GET /v1/voice/history` (`auth.require_scope("read")`).
- **Docker Compose Profiles**: Pinned explicit container image tags (`minio`, `llama.cpp`) and isolated `worker-fast` into an optional `--profile worker-fast`.
- **Cross-Platform Compatibility**: Made Unix-specific `resource` module imports optional with fallback.
- **Whisper Engine Fallback**: Added automated fallback to `small` Whisper model when configured local model directory does not exist on disk.
- **ASR & Audio Preprocessing**: Tuned VAD thresholds and segment overlap to prevent speech chopping, replaced deprecated ffmpeg `-map_channel` with `channelsplit` filter, and expanded outdoor advertising vocabulary prompts.

### Security
- **Per-Hop SSRF Validation (C-01)**: Implemented `download_file_ssrf_safe` with per-hop DNS resolution, redirect validation (max 3 hops), hard streaming byte limit, loopback/private/metadata IP blocking, and disabled redirects in outbound webhooks and callbacks.
- **In-Flight PII Masking (H-04)**: Automated masking of phone numbers, credit card numbers, passport IDs, and email addresses across call transcripts and voice search query history.
- **GDPR Compliance**: Enhanced GDPR data deletion (`POST /v1/admin/gdpr/delete`) to purge user records from both PostgreSQL and Redis voice query history keys.
- **Rate Limiting & Concurrency Control**: Added bounded concurrency semaphore (`asyncio.Semaphore(4)`) on synchronous voice search and Redis-based rate limiting on resource-intensive endpoints.
