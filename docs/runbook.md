# Руководство по эксплуатации сервиса `apc-speech` (Runbook)

Данный документ предназначен для инженеров сопровождения, SRE и дежурных администраторов.
Здесь описаны стандартные процедуры обслуживания, мониторинга и решения инцидентов («Что делать, если...»).

---

## 0. Запуск и остановка

```bash
cp .env.example .env   # один раз; заполнить DEV_TOKEN / API_TOKENS_HASHED / CALLBACK_SIGNING_SECRET
APP_PROFILE=small docker compose up -d --build        # профиль small
APP_PROFILE=local-full docker compose --profile local-full up -d --build  # с LLM

docker compose ps                       # все контейнеры healthy
docker compose up -d --force-recreate api   # подхватить изменённый .env (restart НЕ подхватит)

# Остановка (данные в volumes pgdata/redisdata/miniodata сохраняются):
docker compose stop                     # мягко (SIGTERM, воркеры доканчивают чанк)
docker compose up -d                    # снова поднять
```

Холодный старт `small` до `/ready=200` — около 2,5 мин (прогрев ASR-моделей).
Не используйте `down -v`: ключ `-v` удаляет volumes с БД, очередями и аудио.

---

## 1. Мониторинг состояния и Healthcheck

Сервис предоставляет три стандартных диагностических маршрута:

### 1.1. Liveness Probe (`GET /health`)
Проверяет жизнеспособность процесса API.
```bash
curl -i http://localhost:8000/health
```
* **Ожидаемый ответ**: `200 OK`, `{"status": "ok", "version": "1.0.0", "profile": "small"}`.
* **Действие при сбое (5xx или timeout)**: перезапустить контейнер `api`:
  ```bash
  docker compose restart api
  ```

### 1.2. Readiness Probe (`GET /ready`)
Проверяет доступность всех зависимостей: PostgreSQL, Redis Streams, MinIO S3.
```bash
curl -i http://localhost:8000/ready
```
* **Ожидаемый ответ**: `200 OK`, `{"status": "ready", "dependencies": {"database": true, "redis": true, "s3": true}}`.
* **Если `database: false`**: проверить логи PostgreSQL (`docker compose logs postgres`).
* **Если `redis: false`**: проверить доступность Redis (`docker compose exec redis redis-cli ping`).
* **Если `s3: false`**: проверить статус MinIO (`docker compose logs minio`).

### 1.3. Метрики Prometheus (`GET /metrics`)
Экспортирует счетчики запросов, длительности обработки, количество задач в очереди.
```bash
curl http://localhost:8000/metrics
```

---

## 2. Сценарии инцидентов («Что делать, если...»)

### 2.1. Контейнер падает с ошибкой Out Of Memory (OOMKilled)
* **Симптом**: `docker compose ps` показывает `Exit 137` для `worker-long` или `api`.
* **Причина**: На машине с 4 ГБ RAM была запущена слишком большая модель или несколько параллельных тяжелых файлов.
* **Решение**:
  1. Проверьте `.env`: убедитесь, что `ASR_MODEL_NAME=small` и `ASR_COMPUTE_TYPE=int8`.
  2. Убедитесь, что контейнер `llm-runtime` **не запущен** (профиль `local-full` требует $\ge 16$ ГБ RAM).
  3. Проверьте лимиты в `docker-compose.yml`: для `worker-long` должно быть выделено не более 1800M.

### 2.2. Зависла очередь задач в Redis Streams
* **Симптом**: новые задачи создаются со статусом `queued`, но не переходят в `running`.
* **Диагностика**:
  ```bash
  # Проверить количество задач в стриме
  docker compose exec redis redis-cli XLEN jobs:audio:long

  # Проверить необработанные задачи в группе
  docker compose exec redis redis-cli XPENDING jobs:audio:long group:workers
  ```
* **Решение**:
  1. Проверьте, жив ли воркер: `docker compose ps worker-long`.
  2. Если воркер завис на битом аудиофайле, перезапустите его:
     ```bash
     docker compose restart worker-long
     ```
  3. Задачи, оставшиеся в Pending Entries List (PEL), автоматически будут перехвачены супервизором по истечении 120 секунд.

### 2.3. Не доставляются вебхуки (Callback Worker Failure)
* **Симптом**: платформа не получает уведомления о завершении анализа, в таблице `callback_logs` растут статусы `4xx`/`5xx`.
* **Диагностика**:
  ```sql
  -- Проверить последние ошибки доставки вебхуков
  SELECT job_id, url, attempt, http_status, response_body, sent_at
  FROM callback_logs
  ORDER BY sent_at DESC
  LIMIT 10;
  ```
* **Решение**:
  1. Проверьте сетевую связность из контейнера до URL платформы:
     ```bash
     docker compose exec worker-callback curl -i https://platform.internal/health
     ```
  2. Проверьте заголовок `X-Apc-Signature`: совпадает ли секрет `CALLBACK_SIGNING_SECRET` на стороне сервиса и платформы.
  3. Воркер автоматически делает 5 попыток с экспоненциальной задержкой (до 15 минут).

### 2.4. Ротация токенов авторизации

Полная процедура — в `docs/token-rotation.md` (перекрытие без простоя +
мгновенный отзыв `POST /v1/admin/tokens/revoke`). Кратко: добавить хэш нового
токена в `API_TOKENS_HASHED` рядом со старым, `up -d --force-recreate api`,
переключить клиентов, отозвать старый ручкой, убрать из `.env`.

### 2.5. Принудительная очистка устаревших аудиозаписей в MinIO
* **Симптом**: свободное место на диске подходит к концу.
* **Решение**:
  Используйте эндпоинт каскадного удаления за период:
  ```bash
  curl -X DELETE "http://localhost:8000/v1/data/delete?namespace=outdoor_msk&date_before=2026-09-01T00:00:00Z" \
    -H "Authorization: Bearer <admin-token>"
  ```
  Команда удалит как метаданные из базы данных, так и бинарные файлы из MinIO.

### 2.6. `503 voice_search_timeout` на `POST /v1/voice/search`
* **Симптом**: ответ `503 {"error":"voice_search_timeout"}` + заголовок `Retry-After`.
* **Причины по порядку**: (a) холодный `worker-fast` (модель ещё грузится —
  подождать `/ready=200`); (b) CPU без AVX2 или частота прибита (см. README
  «Требования к системе»); (c) перегруз: больше `VOICE_SEARCH_MAX_CONCURRENCY`
  параллельных запросов — повторить позже по `Retry-After`.
* **Диагностика**:
  ```bash
  docker compose logs --tail=30 worker-fast
  curl -s http://localhost:8000/ready -H "Authorization: Bearer <токен>" | jq .dependencies
  ```

### 2.7. `/ready` — 503 дольше 10 минут
* **Симптом**: `/health` — 200, а `/ready` — 503 с `dependencies.*: false`.
* **Действия**: `database:false` → `docker compose logs postgres` (проверить
  `DATABASE_URL`/пароли); `redis:false` → `docker compose exec redis redis-cli ping`;
  `s3:false` → `docker compose logs minio` (ключи, бакеты). Упавший воркер виден
  в `/ready` не позже ~25 с (heartbeat TTL 20 с). Холодный старт до 2,5 мин — норма.

### 2.8. MinIO: нет бакетов / 403 от S3
* **Симптом**: задания `by-s3` падают, в логах `api` — ошибки S3.
* **Диагностика**:
  ```bash
  docker compose logs --tail=30 minio
  docker compose exec minio mc alias set local http://localhost:9000 "$S3_ACCESS_KEY" "$S3_SECRET_KEY"
  docker compose exec minio mc ls local/
  ```
* **Причины**: разошлись `MINIO_ROOT_USER/MINIO_ROOT_PASSWORD` и `S3_ACCESS_KEY`/
  `S3_SECRET_KEY` (должны совпадать); бакеты `raw-audio`/`exports` не созданы
  (создаются при старте — смотреть лог `api`/init).

### 2.9. `llm-runtime` (только `local-full`): разборы висят или падают
* **Симптом**: задания застревают на шаге анализа, в `worker-long` — таймауты
  обращения к `LOCAL_LLM_URL`.
* **Диагностика**:
  ```bash
  docker compose --profile local-full ps llm-runtime
  docker compose logs --tail=30 llm-runtime
  docker compose exec llm-runtime wget -q -O- http://localhost:8080/health
  ```
* **Причины**: контейнер не поднят (забыт `--profile local-full`); OOM на хосте
  < 16 ГБ (лимит 10240M, факт ~3,5–4 ГБ); модель не докачалась
  (`model-downloader-llm` — смотреть его лог). На профиле `small` разбор идёт
  rule-адаптером — это штат, а не сбой.

---

## 3. Где смотреть логи и метрики

```bash
docker compose logs -f api worker-long worker-fast worker-callback  # живой хвост
docker compose logs --tail=100 <сервис> > /tmp/opencode/<сервис>.log  # снять для разбора
docker stats --no-stream                        # CPU/RAM по контейнерам
curl -s http://localhost:8000/metrics           # Prometheus: apc_http_requests_total,
                                                # apc_http_request_duration_seconds, apc_jobs_total,
                                                # apc_voice_search_duration_seconds, apc_queue_size
```

Логи — JSON с `request_id` (заголовок `X-Request-Id` приходит и в ответе);
тексты расшифровок, телефоны и e-mail в логи не пишутся (guard
`scripts/guards/no_pii_in_logs.py` в `make check`). Очередь живьём:
`docker compose exec redis redis-cli XLEN jobs:audio:long` и
`XPENDING jobs:audio:long group:workers`; DLQ — стрим `jobs:audio:dlq`.
Callback'и — таблица `callback_logs` (см. п.2.3).

---

## 4. Сценарий нагрузочного стресс-тестирования

Скрипт эмуляции параллельной нагрузки (Voice Search параллельно с пакетной транскрибацией):

```bash
# 1. Запуск тестового генератора 20 параллельных запросов Voice Search
for i in {1..20}; do
  curl -s -X POST "http://localhost:8000/v1/voice/search?namespace=outdoor_msk" \
    -H "Authorization: Bearer dev-token" \
    -H "Content-Type: audio/wav" \
    --data-binary "@data/evaluation_suite/sample_clean_01_clean.wav" > /dev/null &
done
wait
echo "Стресс-тест Voice Search завершен успешно!"

# 2. Мониторинг потребления ресурсов
docker stats --no-stream
```
Потребление оперативной памяти контейнерами не должно превышать 3.6 ГБ суммарно на профиле `small`.
