# APC Speech — повторная проверка исправлений

Дата: 2026-10-03. Проверка выполнена после коммита `0f5e407 fix(audit): resolve technical audit findings F-01 through F-15`. Новый dashboard (`app/api/dashboard.py`, `app/static/dashboard-modern.html`) исключён из оценки как отдельный незавершённый проект.

## Итог

Исправления частично успешны. Все штатные тесты и исходные независимые audit-тесты проходят: **138 passed, 1 warning**; Ruff check/format проходят. Реальный isolated Compose `small` поднимает PostgreSQL, Redis, MinIO, API и workers; migration завершается успешно, `/health` и `/ready` отвечают.

Приёмка всё ещё **FAIL/PARTIAL**. Ключевая причина: реальный `local-full` job снова завершается `schema_validation_failed`, теперь на `agreements[0].who = "Клиент"`. Исправление F-02 не закрывает все enum-поля. Кроме того, исходная цель strict schema была ослаблена для `inquiry_type`: JSON Schema теперь принимает любую непустую строку, поэтому F-10 «динамическая taxonomy» достигнута ценой потери ограничения допустимых значений.

## Что действительно исправлено

| Finding | Проверка | Результат |
|---|---|---|
| F-03 idempotency с явным ключом | Повтор invalid upload с `Idempotency-Key: recheck-corrupt` после failed job | PASS: оба ответа вернули один `job_id` |
| F-09 clustering zero vectors | `/v1/texts/analyze` на `['а','я','щит']` | PASS, 200 |
| F-07 envelope | malformed JSON | PARTIAL: тело единообразное `{error,message,details}`, но HTTP 422 вместо требуемого 400 для кривого запроса |
| F-04 latency | 10 warm HTTP requests, 10-sec WAV, 2 CPU container | PASS: p50 1613 ms, p95/p99 2267 ms, max 2267 ms; warmup 2330 ms |
| worker lifecycle | SIGSTOP callback worker, прямой healthcheck | FAIL: healthcheck exit 0 для остановленного процесса |
| model provisioning | configured Qwen URL HEAD; official HF API | FAIL: URL 404; actual model is split into `q4_k_m-00001-of-00002.gguf` and `...00002...` |
| local-full schema | real job `29717372-93ed-4a9f-b24c-031dcf523beb` | FAIL: `agreements[0].who = Клиент` violates enum |
| metrics | isolated `/metrics` | PARTIAL/FAIL: only Python/process metrics, no custom request/job/queue/ASR metrics |
| OpenAPI | static validator + runtime response comparison | PARTIAL: static valid; runtime still advertises only 202/422 for upload and 200/422 for text/voice, while static spec lists operational errors |
| PII/logging | malformed validation containing email/phone | FAIL: request validation log includes raw `client@audit.invalid +7 912 345 67 89` |

## Новые доказанные дефекты

### R-01 — HIGH — local-full всё ещё не проходит end-to-end

Команда:

```text
POST /v1/jobs/upload, sample_clean_01_clean.wav, APP_PROFILE=local-full
GET /v1/jobs/29717372-93ed-4a9f-b24c-031dcf523beb?namespace=recheck
```

Фактически: `failed`, `step=schema_validation_failed`; ошибка: `"Клиент" is not one of ['manager', 'client', 'both']`. Prompt/mapping нормализуют `inquiry_type` и `call_result`, но не `agreements.who` и другие enum-поля.

Рекомендация: использовать canonical slugs в constrained generation для всех enum-полей; затем валидировать/нормализовать каждое поле до persistence и добавить реальный llama.cpp integration test.

### R-02 — CRITICAL — clean-start model downloader сообщает успех после неудачной загрузки и содержит несуществующий URL

`HEAD` на `scripts/download_models.py:17` вернул HTTP 404. Официальный репозиторий содержит два shard-файла, а downloader ожидает один несуществующий файл. `download_qwen()` ловит исключение, печатает `[WARN]`, но `main()` продолжает и печатает `=== All requested models verified ===`, exit code остаётся 0.

Рекомендация: исправить URL/shard handling, добавить checksum/size verification и завершать команду ненулевым кодом при обязательной модели.

### R-03 — HIGH — PII попадает в structured logs через validation errors

Команда с malformed/invalid JSON включала email и телефон. API вернул корректное envelope, но лог `audit_tests/recheck/evidence/api.log` содержит raw input в `exc.errors()`.

Рекомендация: санитизировать `RequestValidationError.errors()` перед логированием или не писать `input`, `ctx` и raw body.

### R-04 — HIGH — spoken phone masking неполна

Повторная проверка показала, что формы `восемь девять один два ...` и `плюс семь девятьсот ... ноль семь ноль девять` остаются без маскирования. Реализованный regex покрывает только формы с четырьмя крупными группами, а не последовательность отдельных цифр/варианты группы.

Рекомендация: парсер spoken digits с точным 10/11-digit state machine, затем единый masking; добавить adversarial corpus.

### R-05 — HIGH — normalization сохраняет false positive

Следующие строки после нормализации превращаются в варианты с `щит`, хотя это бухгалтерский контекст:

```text
пришлите счет на электронную почту -> пришлите щит на электронную почту
счет на имя клиента -> щит на имя клиента
выставьте счет на сумму 25000 рублей -> выставьте щит на сумму 25 000 руб.
```

Negative lookahead не покрывает `имя`, `сумму`, `электронную` и другие контексты. Требование false-positive protection не закрыто.

### R-06 — MEDIUM — malformed JSON имеет неправильный HTTP status

`POST /v1/texts/analyze` с body `{bad` возвращает:

```json
HTTP 422
{"error":"validation_error","message":"Ошибка валидации входных параметров","details":{...}}
```

Тело исправлено, но common requirements требуют 400 для кривого запроса. Нужен отдельный `json_invalid` → 400 mapping.

### R-07 — MEDIUM — production startup только логирует слабый callback secret и продолжает работу

`lifespan()` при `APP_ENV=production` и пустом `CALLBACK_SIGNING_SECRET` пишет `CRITICAL`, но не останавливает startup. Это не «startup hardening»: сервис может подняться с небезопасной конфигурацией.

### R-08 — MEDIUM — idempotency по content hash без ключа всё ещё создаёт новый failed job

Явный ключ теперь сохраняется, но повтор invalid upload без `Idempotency-Key` создал новый job `786ab5e2-d859-4c36-88eb-74f9f8928f78` после ранее failed `34ebaf0b-ca4f-470f-9172-a726981f8114`. Требование допускает dedup по hash+parameters, поэтому поведение остаётся нарушением для failed результата или как минимум не документировано.

### R-09 — MEDIUM — Compose override отменяет заявленное beam-size=1

В `app/core/config.py` default равен 1, но `docker-compose.yml:98` задаёт `VOICE_SEARCH_BEAM_SIZE=${VOICE_SEARCH_BEAM_SIZE:-2}`, а `app/api/voice.py:131` дополнительно делает `max(2, cfg.voice_search_beam_size)`. Реальный isolated container подтвердил `beam=2`. Это делает changelog claim «beam size 1» фактически неверным.

### R-10 — MEDIUM — worker healthcheck можно обмануть SIGSTOP

Выполнен `docker kill --signal STOP apc-recheck-worker-callback-1`; worker state стал `T (stopped)`, но его healthcheck exit code был 0. Проверка ищет только строку в `/proc/*/cmdline`, не проверяя состояние процесса.

### R-11 — MEDIUM — custom observability/retention не реализованы

`/metrics` содержит только default Python/process collectors. В Compose нет scheduler для `scripts/cleanup.py`; retention остаётся ручным. Эти исходные audit findings не закрыты.

### R-12 — MEDIUM — static/runtime contract всё ещё расходится

Static `openapi.yaml` валиден, но runtime `/openapi.json` для upload рекламирует `202,422`, text analyze `200,422`, voice `200,422`; статический контракт содержит operational 400/401/413/429/500. Это не единый source of truth.

### R-13 — MEDIUM — optional fast worker остаётся нестабильным в текущем checkout

В исходном Compose, который был запущен до повторной проверки, `apc-speech-worker-fast-1` имел статус `Exited (137)`. Voice search фактически обслуживается API-потоком, поэтому это не ломает проверенный endpoint, но заявленный отдельный worker не является доказанно рабочим и требует отдельного расследования OOM/жизненного цикла.

## Проверки без новых дефектов

- Штатные тесты плюс прежние audit tests: `138 passed, 1 warning`.
- Ruff: check passed; format check: 79 files formatted.
- Static OpenAPI and JSON Schema validators: valid.
- Isolated Compose small: migration exit 0; API/workers/Postgres/Redis/MinIO started healthy.
- `/health`: 200; `/ready`: 200 with dependencies true.
- Clustering `['а','я','щит']`: 200.
- Explicit idempotency after failure: same job returned.
- Voice benchmark on 2 CPU: p50 1613 ms, p95 2267 ms, max 2267 ms for 10-sec WAV.
- Existing namespace/SSRF/static command-injection checks remain as in baseline; not re-run exhaustively in this pass.

## Не закрыто / не доказано

Full codec matrix, G.711/GSM, 3-hour processing, 1-hour/4-CPU scaling, representative WER/DER/term corpus, callback retries/live HMAC receiver, DB/Redis/MinIO fault injection, SIGTERM active job, full-history gitleaks, pip-audit CVEs, model licenses and DNS-rebinding SSRF remain unverified.

## Acceptance conclusion

Исправления нельзя считать полными. Проект стал лучше: latency, clustering, explicit-key idempotency, error envelope body, dot-phone/common normalizer cases исправлены. Но `local-full` всё ещё не работает end-to-end, model clean-start всё ещё broken, PII может попадать в логи, часть masking/normalization ошибочна, а operational/contract hardening не завершён. Статус: **PARTIAL / NOT ACCEPTED**.

## Evidence

- `audit_tests/recheck/evidence/tests.log`
- `audit_tests/recheck/evidence/regressions.log`
- `audit_tests/recheck/evidence/runtime.log`
- `audit_tests/recheck/evidence/voice-benchmark.json`
- `audit_tests/recheck/evidence/local-full-job.json`
- `audit_tests/recheck/evidence/model-url.txt`
- `audit_tests/recheck/evidence/worker-health.txt`
- `audit_tests/recheck/evidence/contracts.txt`
- `audit_tests/recheck/evidence/api.log`
