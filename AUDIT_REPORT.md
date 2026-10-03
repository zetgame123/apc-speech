# APC Speech — Technical Audit Report

Дата аудита: 2026-10-03. Production-код в рамках аудита не изменялся. Во время работы в checkout присутствовали несвязанные изменения `app/api/dashboard.py`, новый `app/static/dashboard-modern.html` и файл `:memory:.ses`; они исключены из результатов и сохранены.

## 1. Executive Summary

Проект собирается и запускается в текущем рабочем checkout: isolated `docker compose` поднял PostgreSQL, Redis, MinIO, API и два worker-сервиса; `/health` и `/ready` отвечают. Все 97 штатных тестов проходят (`97 passed, 1 warning`), Ruff, форматирование, OpenAPI и JSON Schema проходят.

Приёмка не рекомендована. Реальный `local-full` happy-path не завершается: локальная LLM возвращает русское значение `"Продажи"`, затем worker отклоняет результат фиксированной enum-схемой и переводит job в `failed/schema_validation_failed`. В `small` полный путь на синтетическом аудио завершается, но синхронный voice search на 10-секундном файле измерен в 2841 ms при цели 2500 ms. Повтор failed job с тем же `Idempotency-Key` создаёт новый job.

Дополнительные существенные проблемы: clean checkout не содержит моделей, а `scripts/download_models.py` скачивает только CAM++; `.env.example` содержит рабочие секретные значения по умолчанию; malformed JSON получает FastAPI `detail`, а не единый error envelope; PII masking пропускает dotted и spoken phone forms; нормализатор имеет false positive `счет` → `щит`; clustering падает на коротких текстах; worker healthchecks всегда возвращают успех; runtime OpenAPI отличается от `openapi.yaml`.

## 2. Environment

Проверено на Linux x86_64, Ryzen 7 8845HS (16 logical CPUs), 30 GiB RAM, Docker 29.7.2, Compose 5.5.1, Python 3.12.14 в `.venv`. Для 2-CPU замера использовался `taskset -c 0,1`; контейнер Compose API ограничен 2 CPU и 1280 MiB.

Фактический рабочий Compose на момент аудита был `APP_PROFILE=local-full`; для чистой проверки создан изолированный Compose-проект `apc-audit` с отдельным env, томами и портом `18000`. Isolated build/start завершился успешно: migration `Exited (0)`, API/worker-long/worker-callback/Postgres/Redis/MinIO healthy.

## 3. Architecture Reviewed

FastAPI API → PostgreSQL/pgvector (calls, jobs, transcript, analysis) + Redis Streams (long jobs/callbacks/rate limit) + MinIO (audio). `worker-long` выполняет ffprobe/ffmpeg, faster-whisper, CAM++/эвристическую diarization, PII и LLM/rules analysis. `worker-callback` доставляет HMAC callbacks. `worker-fast` существует, но не является API queue consumer: voice search выполняется в API через `asyncio.to_thread` и semaphore.

Профиль `small` использует локальный ASR/CAM++/rules; `local-full` добавляет llama.cpp/Qwen. GPU не требуется в коде, но заявленные ресурсы модели и runtime не подтверждены на 2 CPU/4 GiB.

## 4. Requirements Compliance Matrix

| Требование | Обязательно | Реализовано | Runtime | Тест/доказательство | Результат | Evidence | Severity |
|---|---:|---:|---:|---|---|---|---|
| Docker/Compose, DB, Redis, MinIO, миграции | Да | Да | Да | isolated Compose | PASS в имеющемся checkout; clean checkout BLOCKED моделями | `audit_tests/evidence/compose-build.log` | HIGH (onboarding) |
| `/health`, `/ready`, `/metrics` | Да | Да | Да | curl на `18000` | PASS; metrics только default Python/process collectors | HTTP evidence в истории аудита | MEDIUM |
| Bearer scopes и namespace isolation | Да | Да | Да | A/B tokens, IDOR checks | PASS для проверенных API | HTTP evidence в истории аудита | — |
| Upload/URL/S3 audio ingestion | Да | Да | upload/SSRF | Да | upload PASS; URL/S3 full path не полностью проверен | `small-upload.json` | NOT TESTED/PARTIAL |
| Форматы wav/mp3/ogg/opus/m4a/flac/webm/телефония | Да | Код | Частично | ffmpeg/preprocessor unit tests | Runtime corpus для всех форматов не выполнен | `app/asr/preprocessor.py` | NOT TESTED |
| Local-only ASR/VAD/diarization | Да | Да | ASR runtime локальный | Code search + offline eval | PASS для observed ASR; network tracing всех путей не проведён | `network-code-search.txt` | PARTIAL |
| WER clean ≤12%, telephony ≤25% | Да | Harness | Да | 1 clean + 1 telephony sample | 0.0% / 11.1%, статистически недостаточно | `evaluation-run-2cpu.log` | NOT ACCEPTANCE-GRADE |
| Terms with hints ≥90% | Да | Hints | Нет | отдельного unbiased term corpus нет | NOT TESTED | manifest/harness | HIGH |
| Stereo/mono diarization, roles, timestamps | Да | Да | Да | synthetic mono job, DER harness | stereo/robust DER and overlap not independently established | `evaluation-run-2cpu.log` | PARTIAL |
| Voice search 10 sec ≤2.5 sec / 2 CPU | Да | Да | Да | real HTTP 10-sec audio | 2841 ms, FAIL | `small-voice10.txt` | HIGH |
| 1-hour processing / 2x on 4 CPU | Да | Harness absent | Нет | 1-hour run not performed | NOT TESTED | docs claim only | HIGH |
| LLM JSON Schema always valid | Да | Да | Да | real local-full job | FAIL: `"Продажи"` violates enum; job failed | job status/logs | HIGH |
| Taxonomy/checklist configurable | Да | Частично | rules tested | dynamic category rejected by fixed schema | PARTIAL | `test_independent.py` | MEDIUM |
| Analytics/text clustering/export | Да | Да | Да | `/v1/texts/analyze` and unit tests | normal dataset PASS; zero-vector edge crashes | independent tests | MEDIUM |
| PII phone/email/card/document masking | Да | Частично | unit/direct | common forms pass, dotted/spoken phones leak | FAIL edge cases | independent tests | HIGH |
| Retention and delete cascade | Да | Да | Delete code | not full object/index/orphan audit | PARTIAL; cleanup is manual script | `scripts/cleanup.py` | MEDIUM |
| Jobs lifecycle/retry/DLQ | Да | Да | Да | real small job and local-full failed job | lifecycle PASS; retry semantics violate idempotency | job evidence | HIGH |
| Idempotency same request | Да | Partial | Да | repeat same key after failure | second job created | `app/api/jobs.py:146-155` | HIGH |
| Callback HMAC, retries ≥5 | Да | Код/unit | callback delivery not live | formula and 5 delays present; live retry not tested | PARTIAL | `callback_dispatcher.py` | MEDIUM |
| Request ID propagation | Да | Да | Да | HTTP response headers | response ID PASS; worker/callback propagation not demonstrated | curl evidence | MEDIUM |
| SSRF protection | Да | Да | Direct/API | local/private/alternate IP probes blocked | PASS for probes; DNS TOCTOU remains | `ssrf-probe.txt` | MEDIUM residual |
| Command injection resistance | Да | Да | Static | subprocess args are arrays | PASS static; hostile filename runtime not exhaustive | `preprocessor.py` | — |
| Malicious media resource limits/isolation | Да | Partial | Нет | ffmpeg timeout/RLIMIT code | no process/network sandbox or concurrent cap proof | `preprocessor.py` | HIGH |
| Rate limiting/backpressure | Да | Да | Partial | code + Redis | 5/10s per token; expensive job cap not demonstrated | `rate_limit.py` | MEDIUM |
| Graceful shutdown | Да | Код | Нет | signal handlers only | active SIGTERM/restart not run | workers | NOT TESTED |
| Non-root/multi-stage/pinned image | Да | Partial | Да | Dockerfile inspect | app non-root/multi-stage; base/apt/uv unpinned | Dockerfile | MEDIUM |
| CI tests/lint/security/secrets | Да | Да | CI config | local tests/lint | gitleaks unavailable; CI does not run live Compose | `.github/workflows/ci.yml` | MEDIUM |
| Documentation matches runtime / 10-minute onboarding | Да | Partial | Да | README vs run | model download and health examples diverge | README/download script | HIGH |
| Commercial licenses | Да | Partial | Нет | local files/license inspect | dependency/model provenance not fully established | LICENSE/uv.lock | NOT TESTED |

## 5. Functional Testing

Проверены health/readiness, text analysis, voice search, upload → Redis → worker → transcript/analysis в `small`, namespace scope errors, malformed JSON, duplicate upload. `small` job `3897803a-1415-427e-b927-7b3eb056c3e8` прошёл `queued → running → succeeded`, анализ и transcript были доступны.

`local-full` job `d1d54a2b-5c41-43ee-83e3-85dbf065f2b5` прошёл ASR/diarization/PII, но завершился `failed`, `step=schema_validation_failed`; ошибка сохранена в job и DLQ.

## 6. API / OpenAPI Audit

Положительное: Bearer, status URLs, multipart upload, cursor list, presigned audio, unified `AppError` для большинства ручных ошибок, `X-Request-Id` в responses.

Дефекты:

* Runtime `/openapi.json` генерируется FastAPI и не совпадает с репозиторным `openapi.yaml`: в runtime отсутствуют описанные custom 400/401/413/429/500 responses, schemas часто `{}`, enum для form `direction/language` не отражён. Static OpenAPI validation поэтому не является contract test runtime.
* JSON parse error возвращает `{"detail":[...]}` с HTTP 422 вместо обязательного `{error,message,details}`. Воспроизведение: `POST /v1/texts/analyze` body `{bad`.
* `/docs` зависит от CDN `cdn.jsdelivr.net`; в закрытом контуре UI не сможет загрузить Swagger assets. В production clean config docs отключён (404), что допустимо только если это явно принято.
* Form fields `direction`, `language`, `channel_manager` не имеют полноценной смысловой валидации; unknown fields у Pydantic моделей игнорируются.

## 7. Authentication & Authorization

Проверены missing/invalid token, `read` vs `write`, namespace-scoped tokens. A-token не получил B, B-token не получил A, read-token не вызвал write endpoint. Revocation хранится в памяти + Redis set. Query-string tokens разрешены в Bitrix integration (`integrations.py:38-60`), что создаёт риск утечки в access logs и нарушает единый Bearer-подход.

## 8. Namespace Isolation

Проверенные call/job/analysis/transcript endpoints используют `namespace` в SQL и `AuthContext.verify_namespace`. Cross-namespace IDOR на реальном call вернул 403/404. Полный callback/search/export/delete matrix не запускался на populated A/B corpus — статус PARTIAL.

## 9. Audio Ingestion

Streaming upload и SHA-256 дедупликация работают. S3/URL routes существуют. Runtime corpus всех заявленных кодеков, G.711/GSM, corrupt/polyglot/3-hour files не выполнен; это нельзя считать PASS.

## 10. ASR Quality

`evaluation.run` на 2 CPU: clean 1 sample WER 0.0%/CER 0.0%; telephony 1 sample WER 11.1%/CER 5.7%; voice harness 2277 ms для имеющегося sample. Это маленький synthetic corpus, не доказательство acceptance WER или term ≥90%. Реальный API на 10-sec controlled audio: 2841 ms.

## 11. VAD

Whisper VAD parameters и rule answering-machine detector присутствуют. Музыка, тишина, IVR и длинные паузы на независимом controlled corpus не проверены.

## 12. Diarization

CAM++ model loaded in real worker; mono synthetic job produced one manager segment. Existing evaluation reports DER 0.0% on one synthetic dialogue, but harness DER is role-confusion only and sample size is one; overlap/interruption/stereo separated-channel corpus not independently measured.

## 13. Timestamp Accuracy

Observed transcript has monotonic non-negative segment/word timestamps and duration within audio length. Long/chunk boundary monotonicity and 3-hour coverage not tested.

## 14. Normalization

Independent audit tests found required gaps: `двадцать пять тысяч рублей`, `тринадцатого октября`, spoken phone words remain unchanged. False positive: `пришлите счет на оплату` becomes `пришлите щит на оплату`. This violates both required coverage and false-positive constraint.

## 15. Terminology / Hints

Hints are wired to Whisper `initial_prompt`/`hotwords`, but no independent before/after term accuracy corpus exists. The claim 94.2% in docs cannot be reproduced from the one clean/one telephony manifest.

## 16. LLM Analysis

External adapter is disabled by default and asserts masked PII before HTTP. Local adapter requests JSON schema, but prompt says all strings must be Russian while `inquiry_type`, `call_result`, role fields use English enum slugs. Real local-full response exposed this contradiction (`"Продажи"`) and job failed strict validation. JSON Schema is therefore not “always” satisfied end-to-end.

## 17. Taxonomy & Checklist

All draft slugs exist in `config/taxonomy.yaml`; checklist exists. Rule analysis uses configured keywords. Adding a new category without changing code produces an enum validation failure, so “taxonomy configurable without code” is only true for existing enum values/keywords.

## 18. Analytics & Clustering

`/v1/texts/analyze` grouped a 3-text dataset correctly. Independent test `['а','я','щит']` raises `ValueError: Cosine affinity cannot be used when X contains zero vectors` in AgglomerativeClustering. Empty/duplicate cases pass.

## 19. PII / Privacy

Common phone/email/card/RF passport forms mask. Dotted phone `8.912.345.67.89` and spoken phone words remain unchanged. Raw `text_original` and `words_json` are stored in PostgreSQL; `masked=false` intentionally exposes them to an authorized reader. With `DEBUG=true`, SQLAlchemy echo logs raw transcript/words (observed in worker logs), violating the no-PII logging rule if debug is enabled in a deployed environment.

## 20. Storage & Retention

Audio objects, DB calls, transcript, analysis and delete endpoint exist. Retention cleanup is only `scripts/cleanup.py`; no Compose scheduler/worker invokes it. Full orphan-object, search-index and period-delete consistency test was not completed.

## 21. Jobs / Queues

Redis Streams groups, XAUTOCLAIM, retries and DLQ are present. Real small job lifecycle succeeded. Failed local-full job was marked failed and moved to DLQ.

## 22. Idempotency

For successful/in-flight jobs same key returns existing job. For failed job code deliberately rewrites the key (`app/api/jobs.py:146-155`, `333-341`, `479-486`) and creates another job. Reproduction: first upload with `Idempotency-Key: audit-sample-1` failed; identical second upload returned a different job `96a7c74f-d923-4886-ba05-a90c1f031f36`. This violates the requirement that repeating the same request cannot create a second result.

## 23. Callback Security

`sign_callback_payload` implements `sha256=HMAC_SHA256("{timestamp}.{raw_body}", secret)` and dispatcher has 5 delays. Live callback receiver/retry/429/duplicate tests were not run. Callback URL validation has a DNS validation/use TOCTOU window.

## 24. SSRF

`localhost`, loopback, RFC1918, metadata IP, IPv4-mapped IPv6, integer/legacy loopback and non-http schemes were blocked in direct probes and API URL upload. DNS is resolved once in `validate_ssrf_url`, then the HTTP client resolves again; DNS rebinding is not eliminated.

## 25. Command Injection

ffmpeg/ffprobe calls use argument arrays, `-nostdin`, timeouts and no shell. No shell metacharacter path reached a shell in static review. Runtime hostile filename corpus not exhaustive.

## 26. Malicious Files

RLIMIT_CPU/AS and subprocess timeouts exist. There is no separate parser process/container, no explicit network namespace for ffmpeg, and no global concurrent audio-job cap. Truncated, huge-header, decompression/pathological codec and disk-pressure tests were not completed.

## 27. Rate Limiting

Redis fixed 10-second window limits requests to 5 per token/IP and emits 429/Retry-After. It is applied to expensive routes but not every public route, and job concurrency/backpressure caps were not demonstrated. Redis errors are fail-open.

## 28. Resource Exhaustion

Upload bytes are streamed and voice search has 10 MiB cap/semaphore 4. Long jobs have no configured admission cap beyond one worker; 3-hour/500-MB and simultaneous stress tests were not run.

## 29. Docker Security

Application image is multi-stage and runs as `apcuser`. Base image, apt packages, and uv installer are unpinned. Worker/fast healthchecks execute `sys.exit(0)` unconditionally, so a crashed/hung worker can remain “healthy”. Compose has no `cap_drop`, read-only rootfs, explicit network isolation or secrets mechanism. `llm-runtime` publishes unauthenticated port 8080 on all host interfaces.

## 30. Dependency Security

`uv.lock` exists and local Ruff/tests pass. `pip-audit` could not query PyPI because the audit environment had no DNS/network; no vulnerability result is claimed. `pyproject.toml` uses broad `>=` ranges even though lock resolves them.

## 31. Secrets / Git History

`.env` and models are ignored and not in `git ls-tree`; no obvious secret was found in tracked HEAD. `gitleaks` is not installed locally, so full-history scan is NOT TESTED. `.gitleaks.toml` allowlists documentation tokens.

## 32. Logging

Structlog JSON output and request_id middleware exist. `DEBUG=true` enables SQLAlchemy echo and observed raw transcript/word JSON in worker logs. Callback/Bitrix URL values are logged; query-string integration tokens can therefore reach logs.

## 33. Metrics / Observability

`/metrics` is valid Prometheus text, but observed output contains only default Python/process metrics; custom request/job/queue/ASR duration/error counters are not exposed. Worker healthchecks mask process liveness.

## 34. Performance

Measured:

| Scenario | Environment | Result | Target | Status |
|---|---|---:|---:|---|
| Voice API, 4.37 s audio | API container, warm, 2 CPU limit | 3105 ms | 10 s ≤2500 ms | FAIL/unsupported sample |
| Voice API, controlled 10 s | API container, warm, 2 CPU limit | 2841 ms | ≤2500 ms | FAIL |
| Evaluation harness voice sample | host taskset 2 CPU, warm | 2277 ms | ≤2500 ms | PASS for this shorter sample |
| Clean WER | 1 synthetic sample | 0.0% | ≤12% | insufficient sample |
| Telephony WER | 1 synthetic sample | 11.1% | ≤25% | insufficient sample |
| 1-hour ASR | not run | N/A | ≤1 h / 2 CPU | NOT TESTED |
| 4 CPU scaling | not run | N/A | ~2x | NOT TESTED |

## 35. Fault Injection

DB/Redis/MinIO outage, worker SIGTERM during active job, callback 500/429, disk full and memory pressure were not actively injected. Readiness has dependency checks and job failures are persisted, but graceful recovery remains unproven.

## 36. CI/CD

CI runs Ruff, OpenAPI/schema validation, pytest, model download and evaluation, plus Gitleaks action. It does not start Compose for API/worker integration, does not run a real security scanner result, and evaluation depends on downloading a model/network. Local gitleaks was unavailable.

## 37. Documentation

README architecture is detailed, but onboarding says `scripts/download_models.py` downloads Whisper and Qwen; the script only downloads CAM++. README health example claims `status: healthy`/`app_profile`/`llm_model`, while runtime returns `status: ok`, `version`, `profile`. README says small/local-full resources inconsistent with common requirements. `docs/known-issues.md` acknowledges several limits but not the observed schema/idempotency failures.

## 38. Licensing

Repository LICENSE is MIT. Full transitive dependency, ffmpeg, CAM++, Whisper and Qwen commercial-license provenance was not independently verified; no PASS is claimed.

## 39. Findings

| ID | Severity | Category | Finding | Evidence | Reproduction | Fix |
|---|---|---|---|---|---|---|
| F-01 | CRITICAL | Onboarding/ASR | Clean checkout lacks required Whisper/CAM++/Qwen assets; downloader only fetches CAM++ | `git ls-tree`, `scripts/download_models.py:10-43` | clone; follow README; run downloader/compose | versioned model artifact registry or verified offline bundle; fail fast with documented model provisioning |
| F-02 | HIGH | LLM/schema | `local-full` real job fails strict schema because prompt/LLM returns Russian enum `Продажи` | job `d1d54...`, worker logs, `schemas/analysis_schema_v1.json` | upload clean WAV in local-full | use slugs in prompt/grammar, validate/repair before persistence, add integration test against llama.cpp |
| F-03 | HIGH | Idempotency | Failed request key is rewritten and duplicate job is created | `app/api/jobs.py:146-155`; two job IDs | repeat same upload/key after failure | preserve immutable idempotency record; explicit retry endpoint/key |
| F-04 | HIGH | Performance | Real 10-second voice search takes 2841 ms on 2-CPU API container | `small-voice10.txt` | POST 10-sec WAV | optimize model/preprocessing, dedicated worker/capacity, measure p95 on declared hardware |
| F-05 | HIGH | Privacy | PII masker misses dotted and spoken phone numbers | independent audit test output | call `PiiMasker.mask_text` with those forms | normalize phone formats/number words before regex; add adversarial corpus |
| F-06 | HIGH | Supply chain | README clean-start model provisioning is false; Compose relies on ignored local models and unpinned downloads | README:305-344; downloader | fresh checkout | pin/download checksums and document model artifact setup |
| F-07 | MEDIUM | Contract | malformed JSON/validation uses FastAPI `detail`, not required error envelope | HTTP 422 response | POST `{bad` | global RequestValidationError handler with `{error,message,details}` |
| F-08 | MEDIUM | Normalization | required amounts/dates/spoken phone forms fail; `счет` false-positive becomes `щит` | independent tests | direct normalizer calls | context-aware normalization with regression corpus |
| F-09 | MEDIUM | Clustering | zero-vector short texts crash cosine agglomerative clustering | independent test stack trace | POST texts `['а','я','щит']` | detect zero vectors/use Euclidean or single-cluster fallback |
| F-10 | MEDIUM | Config/schema | adding taxonomy category without code violates fixed schema enum | independent dynamic category test | edit taxonomy and analyze | schema enum generated from validated config or keep taxonomy closed explicitly |
| F-11 | MEDIUM | Secrets/config | `.env.example` contains non-empty DB/MinIO/password/callback secret defaults | `.env.example:...` | copy example and run | empty placeholders; startup reject weak production secrets |
| F-12 | MEDIUM | Observability | worker healthchecks always succeed; custom metrics absent | `docker-compose.yml:143,173,200`; `/metrics` | kill/hang worker; inspect metrics | real health probe and request/job/queue metrics |
| F-13 | MEDIUM | Contract/docs | runtime OpenAPI differs from static `openapi.yaml`; README health response differs | `/openapi.json`, README:350-365 | compare schemas/responses | generate/validate one source of truth in CI |
| F-14 | MEDIUM | Retention | cleanup only manual script; no scheduled execution/orphan audit | `scripts/cleanup.py` | wait past retention | scheduled worker/job, object/DB consistency checks |
| F-15 | LOW | Hardening | CDN Swagger assets, broad image/system package pins, fail-open Redis rate limit | `/docs`, Dockerfile, rate_limit.py | closed-network docs / Redis outage | self-host docs assets, pin digest/packages, explicit fail-open policy |

## 40. Recommended Fix Order

* **P0:** F-01/F-06 model provisioning; F-02 local-full schema; F-03 idempotency; F-05 PII leak; disable debug SQL echo in production and remove secrets from examples.
* **P1:** F-04 latency; F-07 error envelope; F-08 normalization; F-12 worker health/metrics; F-13 contract generation; active retention scheduler and malicious-media isolation.
* **P2:** F-09 clustering, F-10 taxonomy/schema design, callback/DNS hardening, 1-hour/4-CPU benchmarks, full codec/security corpus.
* **P3:** docs CDN/offline UI, image/package digest hardening, richer observability and license inventory.

## 41. Remaining Risks

WER/DER/term accuracy claims are based on one sample per class and cannot support production acceptance. 1-hour processing, 4-CPU scaling, full codec matrix, callback delivery/retries, SIGTERM, DB/queue fault injection, malicious files, full-history secrets scan, dependency CVEs and model licenses remain unverified.

## 42. Test Inventory

* Existing: 97 tests, all passed, one sklearn FutureWarning.
* Independent temporary: `audit_tests/test_independent.py` — 30 passed, 10 intentionally exposed baseline failures (normalization, PII, clustering, schema/config).
* Static: Ruff check/format, OpenAPI validator, JSON Schema checker passed.
* Runtime: isolated Compose health/readiness/metrics/docs, voice search, upload/job lifecycle, analysis/transcript, scope/namespace, malformed JSON, SSRF.

## 43. Benchmark Results

See `audit_tests/evidence/evaluation-run-2cpu.log`, `small-voice10.txt`, `small-job-status.jsonl`. The repository `docs/benchmark-report.md` claims broader clean/open/phone datasets and hardware conditions that are not represented by the checked-in manifest or reproducible local run; those claims are treated as unverified.

## 44. Final Acceptance Status

1. Чистая машина: **NO/UNPROVEN** — current checkout works, fresh checkout lacks models.
2. `docker compose up`: **PASS in current checkout; BLOCKED for clean model provisioning**.
3. `/health`: **PASS**.
4. `/ready`: **PASS** with DB/Redis/MinIO.
5. Full happy-path: **PARTIAL** — small PASS, local-full FAIL.
6. All mandatory requirements: **FAIL**.
7. ASR local: **PASS for observed pipeline; PARTIAL network audit**.
8. Audio external leakage: **no observed ASR provider; not exhaustively traced**.
9. Diarization: **PARTIAL**.
10. WER targets: **NOT ACCEPTANCE-GRADE / PARTIAL**.
11. Terminology ≥90%: **NOT TESTED**.
12. Voice latency: **FAIL on real 10-sec API measurement**.
13. Hour processing: **NOT TESTED**.
14. LLM JSON Schema: **FAIL in local-full**.
15. Analytics: **PARTIAL** (edge crash).
16. Namespace isolation: **PASS for tested routes**.
17. PII masking: **FAIL edge cases**.
18. Deletion/retention: **PARTIAL**.
19. Idempotency: **FAIL after failed job**.
20. Callback signing: **PARTIAL; formula present, live retries untested**.
21. SSRF: **PASS for probes; DNS rebinding residual**.
22. Command injection: **PASS static review**.
23. Malicious files: **PARTIAL**.
24. Critical security issues: **No confirmed RCE/auth bypass; PII/config/clean-start risks are HIGH**.
25. Graceful shutdown: **NOT TESTED**.
26. CI: **PASS static configuration; no full Compose integration**.
27. Secrets in history: **NOT FULLY TESTED; gitleaks unavailable**.
28. Documentation: **PARTIAL/FAIL where model and health claims diverge**.
29. Licensing: **NOT TESTED fully**.

## Audit Artifacts

Created only audit artifacts: `AUDIT_REPORT.md`, `audit_tests/test_independent.py`, `audit_tests/prepare_environment.py`, isolated compose/corpus and evidence logs under `audit_tests/`. Generated `audit_tests/audit.env` was removed after teardown because it contained temporary test credentials. Production application files were not changed by the audit; unrelated working-tree dashboard changes were preserved and excluded.
