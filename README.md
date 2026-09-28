# Сервис анализа речи (`apc-speech`)

Автономный сервис локального распознавания русской речи, диаризации, семантического анализа телефонных звонков и голосового поиска для платформы наружной рекламы.

## Архитектура в одном абзаце

Сервис построен по принципу **Modular Monolith with Dedicated Workers**. Распознавание речи (`faster-whisper`, `Silero VAD v5`, диаризация) работает **строго локально**, аудиофайлы никогда не покидают сервер. Короткие голосовые запросы ($\le 60$ с) обслуживаются быстрым воркером `worker-fast` прямо из оперативной памяти ($\le 2{,}5$ с). Длинные телефонные звонки (до 3 часов) обрабатываются через очередь Redis Streams воркером `worker-long`. Метаданные, русскоязычный полнотекстовый поиск и векторные эмбеддинги тем хранятся в **PostgreSQL 16 с расширением pgvector**. Опциональный внешний LLM-провайдер отключен по умолчанию, изолирован интерфейсом-портом и физически способен получать только деперсонализированный (PII-masked) текст.

Подробный дизайн-документ: [docs/architecture.md](docs/architecture.md)  
Архитектурные решения (ADR): [docs/decisions.md](docs/decisions.md)

---

## Быстрый старт за 1 минуту

### 1. Подготовка конфигурации
```bash
cp .env.example .env
```
Отредактируйте `.env` и задайте безопасные значения, в частности:
- Убедитесь, что `DEV_TOKEN` установлен в надежное значение или очищен (по умолчанию он пуст, что отключает dev-доступ). Для локальной разработки можете установить `DEV_TOKEN=dev-token`.
- Измените `CALLBACK_SIGNING_SECRET`.

### 2. Запуск комплекса (Профиль `small`: 2 vCPU / 4 GB RAM, без GPU)
```bash
docker compose up -d --build
```

### 3. Запуск с локальной LLM (Профиль `local-full`: 8 vCPU / 32 GB RAM или GPU)
```bash
docker compose --profile local-full up -d --build
```

### 4. Проверка работоспособности
```bash
# Проверка жизнеспособности (Liveness)
curl http://localhost:8000/health

# Проверка готовности зависимостей (Readiness: Postgres, Redis, MinIO, ASR)
curl http://localhost:8000/ready
```

---

## Примеры использования API

### 1. Голосовой поиск (Voice Search $\le 2{,}5$ с)
```bash
curl -X POST "http://localhost:8000/v1/voice/search?namespace=outdoor_msk" \
  -H "Authorization: Bearer dev-token" \
  -H "Content-Type: audio/wav" \
  --data-binary "@test_query.wav"
```

### 2. Загрузка телефонного звонка на полный анализ
```bash
curl -X POST "http://localhost:8000/v1/jobs/upload" \
  -H "Authorization: Bearer dev-token" \
  -F "namespace=outdoor_msk" \
  -F "file=@call_record.mp3" \
  -F "external_id=call_789" \
  -F "callback_url=https://platform.internal/webhooks/speech"
```
Ответ: `202 Accepted` с `job_id`.

### 3. Опрос статуса задания
```bash
curl -X GET "http://localhost:8000/v1/jobs/{job_id}?namespace=outdoor_msk" \
  -H "Authorization: Bearer dev-token"
```

### 4. Получение транскрипта
```bash
# JSON с таймкодами слов и ролями участников (маскированный)
curl -X GET "http://localhost:8000/v1/calls/{call_id}/transcript?namespace=outdoor_msk&masked=true" \
  -H "Authorization: Bearer dev-token"

# Экспорт в субтитры SRT
curl -X GET "http://localhost:8000/v1/calls/{call_id}/transcript?namespace=outdoor_msk&format=srt" \
  -H "Authorization: Bearer dev-token"
```

---

## Структура репозитория

```
.
├── app/                        # Исходный код сервиса
│   ├── api/                    # Маршруты FastAPI, middleware, auth
│   ├── core/                   # Конфигурация Pydantic, логгер, безопасность
│   ├── db/                     # Модели SQLAlchemy, подключение, репозитории
│   ├── storage/                # MinIO S3 клиент с управлением retention
│   ├── queue/                  # Redis Streams producer/consumer
│   ├── asr/                    # FFmpeg, Silero VAD, faster-whisper, ITN
│   ├── diarization/            # Стерео-сплит, моно-диаризация (MFCC + KMeans), эвристики ролей
│   ├── metrics/                # Детерминированные акустические метрики (WPM, паузы)
│   ├── pii/                    # Regex + Natasha NER маскирование ПДн
│   ├── llm/                    # Порт семантического анализа и адаптеры (Local/External)
│   └── workers/                # Точки входа воркеров (fast, long, callback)
├── config/                     # YAML-конфиги таксономии, чек-листа и подсказок
├── docs/                       # Архитектурная документация и ADR
├── evaluation/                 # Оценочный стенд (WER, DER, F1-score)
├── schemas/                    # JSON-схемы (analysis_schema_v1.json)
├── tests/                      # Юнит, интеграционные и контрактные тесты
├── openapi.yaml                # Спецификация контракта OpenAPI 3.1
├── Dockerfile                  # Multi-stage сборка без root-прав
└── docker-compose.yml          # Оркестрация полного стека
```

---

## Эксплуатация и Troubleshooting

* **Что делать, если задача зависла?**  
  Супервизор воркеров автоматически возвращает не подтвержденные задачи через Redis Pending Entries List (PEL) по истечении 120 секунд таймаута аренды.
* **Что делать, если памяти $< 4$ GB и процесс падает с OOM?**  
  Убедитесь, что параметр `ASR_MODEL_NAME=small` и `ASR_COMPUTE_TYPE=int8` в `.env`. Профиль `small` не должен запускать `llm-runtime`.
* **Как протестировать локально без Docker?**  
  Установите зависимости через `uv sync` и запустите тесты:
  ```bash
  pytest -v
  ```

### Очистка устаревших данных
Для удаления данных по истечении сроков хранения запускайте скрипт:
```bash
docker compose exec api python3 -m scripts.cleanup
```
