# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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
