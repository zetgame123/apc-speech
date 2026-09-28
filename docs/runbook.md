# Руководство по эксплуатации сервиса `apc-speech` (Runbook)

Данный документ предназначен для инженеров сопровождения, SRE и дежурных администраторов.
Здесь описаны стандартные процедуры обслуживания, мониторинга и решения инцидентов («Что делать, если...»).

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
* **Симптом**: плановая смена API-ключа платформы.
* **Порядок действий**:
  1. Сгенерируйте новый случайный токен: `openssl rand -hex 32`.
  2. Получите его SHA-256 хэш:
     ```bash
     echo -n "новый_токен" | sha256sum
     ```
  3. Добавьте хэш нового токена через запятую в переменную `API_TOKENS_HASHED` в `.env` рядом со старым.
  4. Примените конфигурацию без простоя:
     ```bash
     docker compose up -d --no-deps api
     ```
  5. Переключите платформу на новый токен, после чего удалите старый хэш из `.env`.

### 2.5. Принудительная очистка устаревших аудиозаписей в MinIO
* **Симптом**: свободное место на диске подходит к концу.
* **Решение**:
  Используйте эндпоинт каскадного удаления за период:
  ```bash
  curl -X DELETE "http://localhost:8000/v1/data/delete?namespace=outdoor_msk&date_before=2026-09-01T00:00:00Z" \
    -H "Authorization: Bearer <admin-token>"
  ```
  Команда удалит как метаданные из базы данных, так и бинарные файлы из MinIO.

---

## 3. Сценарий нагрузочного стресс-тестирования

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
