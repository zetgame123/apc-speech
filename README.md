# `apc-speech` — локальный сервис анализа речи для наружной рекламы

Сервис принимает записи телефонных звонков (файл, ссылка, S3), распознаёт русскую речь, разделяет говорящих («менеджер»/«клиент»), строит разбор по JSON-схеме и сводную аналитику, а короткие голосовые запросы (до 60 с) распознаёт синхронно. Плюс — голосовой поиск, дашборд и коннектор Битрикс24.

Ключевые принципы: распознавание речи — только локально (внешних ASR/VAD нет в коде вообще); аудио никогда не покидает сервер; внешний провайдер аналитики — отдельный адаптер за портом, выключен по умолчанию (`LLM_EXTERNAL_PROVIDER_ENABLED=false`) и получает только маскированный текст; секреты — только через env.

## Оглавление

1. [Быстрый старт](#быстрый-старт)
2. [Профили и конфигурация](#профили-и-конфигурация)
3. [API](#api)
4. [Качество и скорость](#качество-и-скорость)
5. [Дашборд](#дашборд)
6. [Безопасность и данные](#безопасность-и-данные)
7. [Эксплуатация](#эксплуатация)
8. [Лицензии третьих сторон](#лицензии-третьих-сторон)
9. [Тесты и проверки](#тесты-и-проверки)

Архитектура и устройство пайплайна — в [`docs/architecture.md`](docs/architecture.md), детали алгоритмов — в коде (`app/asr/`, `app/diarization/`, `app/llm/`, `app/pii/`).

---

## Быстрый старт

### Требования

- Linux x86_64, Docker Engine 24+ с Compose v2.
- Профиль `small`: 2 vCPU / 4 ГБ RAM, без GPU, ~10 ГБ на диске. Профиль `local-full`: 8 vCPU / 32 ГБ RAM или GPU от 12 ГБ, ~20 ГБ на диске.
- Процессор обязательно с AVX2: `grep -o avx2 /proc/cpuinfo | head -1`. Без AVX2 int8-инференс деградирует на порядок, ориентиры скорости недостижимы. Сервис сам сообщает флаг в `GET /health` → `details.avx2`.

### Запуск

```bash
git clone https://github.com/zetgame123/apc-speech.git
cd apc-speech
cp .env.example .env
```

В `.env` обязательны (остальное — по таблице ниже):

```env
APP_ENV=development          # тестовый стенд; для production — production
DEV_TOKEN=<случайная-строка> # только для APP_ENV != production; даёт полный доступ
CALLBACK_SIGNING_SECRET=<минимум-32-символа>  # HMAC-подпись исходящих callback
```

Для production вместо `DEV_TOKEN` (он там игнорируется, даст `401`): `APP_ENV=production` + `API_TOKENS_HASHED="<sha256_токена>:admin+write+read:<namespace>"`, плюс сменить `DB_PASSWORD` и `MINIO_ROOT_PASSWORD`/`S3_SECRET_KEY` парами. Выпуск токена и формат — в [разделе API](#api) и [`docs/token-rotation.md`](docs/token-rotation.md).

```bash
# small (без LLM):
APP_PROFILE=small docker compose up -d --build
# local-full (с Qwen 2.5 7B через llama.cpp):
APP_PROFILE=local-full docker compose --profile local-full up -d --build
```

Модели качаются сами одноразовыми джобами `model-downloader` (~2,8 ГБ) и `model-downloader-llm` (~4,4 ГБ, только `local-full`); повторные запуски ничего не перекачивают. После правок `.env` контейнеры надо пересоздать (`up -d --force-recreate ...`), простой `restart` переменные не подхватит. Не используйте `down -v` на рабочем стеке — удалит volumes с БД, очередями и аудио.

### Проверка и первый запрос

Команды повторяют `scripts/clean_machine_check.sh` (холодный старт `small` до `/ready=200` — около 2,5 мин, это прогрев моделей):

```bash
TOKEN=<ваш-DEV_TOKEN>; BASE=http://localhost:8000
curl -s $BASE/health                                   # 200 {"status":"ok",...}
curl -s $BASE/ready -H "Authorization: Bearer $TOKEN"  # 200; первые минуты допустим 503
curl -s $BASE/metrics | head -20                       # 200, prometheus-метрики

# первый звонок: тестовый wav генерирует сам сервис
curl -s $BASE/v1/voice/sample-audio -H "Authorization: Bearer $TOKEN" -o sample.wav
R=$(curl -s -X POST "$BASE/v1/jobs/upload" -H "Authorization: Bearer $TOKEN" \
  -F "namespace=outdoor_msk" -F "file=@sample.wav" \
  -F "external_id=demo-001" -F "direction=inbound"); echo "$R"
# 202, есть job_id и status_url
JOB=$(echo "$R" | python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])')
for i in $(seq 1 40); do
  S=$(curl -s "$BASE/v1/jobs/$JOB?namespace=outdoor_msk" -H "Authorization: Bearer $TOKEN")
  echo "$S" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])'
  echo "$S" | grep -q '"status": *"succeeded"' && break; sleep 5
done
CALL=$(curl -s "$BASE/v1/jobs/$JOB?namespace=outdoor_msk" -H "Authorization: Bearer $TOKEN" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["call_id"])')
curl -s "$BASE/v1/calls/$CALL/transcript?namespace=outdoor_msk&format=json" \
  -H "Authorization: Bearer $TOKEN" | head -c 500; echo
curl -s -X POST "$BASE/v1/voice/search?namespace=outdoor_msk&language=ru" \
  -H "Authorization: Bearer $TOKEN" -F "file=@sample.wav;type=audio/wav"
# 200 и поле text; перегрузка/таймаут — 503 voice_search_timeout + Retry-After
```

Полный сквозной сценарий на каждую функцию — в [`docs/acceptance-requests.md`](docs/acceptance-requests.md). Если что-то пошло не так (`/ready` 503 дольше 10 мин, `429`, `503 voice_search_timeout`) — сначала раздел «Что делать, если...» в [`docs/runbook.md`](docs/runbook.md).

---

## Профили и конфигурация

| Параметр | `small` (дефолт) | `local-full` |
|---|---|---|
| Железо | 2 vCPU / 4 ГБ, без GPU | 8 vCPU / 32 ГБ или GPU от 12 ГБ |
| ASR / диаризация | faster-whisper int8 + CAM++ локально | то же |
| Разбор звонка и роли | rule-based адаптер (YAML-таксономия) | Qwen 2.5 7B через `llm-runtime` |
| Запуск | `APP_PROFILE=small docker compose up -d --build` | `APP_PROFILE=local-full docker compose --profile local-full up -d --build` |

Переменные окружения (дефолты — из [`.env.example`](.env.example), они же действуют в compose-стеке):

| Переменная | Дефолт | Назначение |
|---|---|---|
| `APP_PROFILE` | `small` | `small` / `local-full` |
| `APP_ENV` | `production` | `development` — DEV_TOKEN; `production` — только хэши токенов¹ |
| `LOG_LEVEL` / `DEBUG` / `BUILD_VERSION` | `INFO` / `false` / `0.1.0` | Логи, отладка, версия в `/health` |
| `HOST` / `PORT` / `WORKERS` | `0.0.0.0` / `8000` / `1` | HTTP-сервер |
| `API_TOKENS_HASHED` | `""` | `sha256:scope1+scope2:namespace` через запятую; единственный способ входа в production |
| `DEV_TOKEN` | `""` | Токен открытого вида для не-production² |
| `CALLBACK_SIGNING_SECRET` | `""` | HMAC-секрет callback, минимум 32 символа (в production короткий/дефолтный роняет старт) |
| `CALLBACK_ALLOW_PRIVATE_NET` | `false` | Разрешать приватные адреса для callback; включённый в production блокирует старт |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:3000,http://localhost:8000` | В production сузить до доменов фронтенда |
| `DATABASE_URL` / `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | `postgresql+asyncpg://apc_user:<CHANGE_PASSWORD>@postgres:5432/apc_speech` / `10` / `5` | Подключение Postgres |
| `DB_USER` / `DB_PASSWORD` | `""` (= `apc_user` / `secure_pass`) | Пользователь Postgres, должны совпадать с `DATABASE_URL` |
| `REDIS_URL` / `REDIS_MAX_CONNECTIONS` / `REDIS_SOCKET_TIMEOUT_SEC` | `redis://redis:6379/0` / `20` / `30` | Redis; сокет-таймаут должен превышать `VOICE_SEARCH_RESULT_TIMEOUT_SEC + 5` |
| `S3_ENDPOINT_URL` / `S3_ACCESS_KEY` / `S3_SECRET_KEY` | `http://minio:9000` / `""` / `""` (= `minio_admin` / `minio_secret_pass`) | S3-хранилище; ключи должны совпадать с `MINIO_ROOT_*` |
| `S3_BUCKET_AUDIO` / `S3_BUCKET_EXPORTS` / `S3_REGION` | `raw-audio` / `exports` / `us-east-1` | Бакеты аудио и экспортов |
| `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` / `MINIO_IMAGE` | `""` / `""` / `""` | Root MinIO (= S3-ключи); переопределение образа для закрытого registry |
| `ASR_MODEL_NAME` / `ASR_COMPUTE_TYPE` / `ASR_DEVICE` / `ASR_THREADS` | `./models/whisper-podlodka-turbo-ct2` / `int8` / `cpu` / `2` | Длинный ASR-путь |
| `VAD_THRESHOLD` | `0.5` | Порог VAD |
| `MAX_AUDIO_DURATION_HOURS` / `MAX_UPLOAD_SIZE_MB` | `3` / `500` | Жёсткие лимиты входа |
| `MAX_PENDING_JOBS` | `100` | Сверх лимита `queued`/`running` — `429` + `Retry-After` |
| `PENDING_DOWNLOAD_TIMEOUT_SEC` | `600` | Зависшие `pending_download` уходят в `failed` с callback |
| `VOICE_SEARCH_MAX_DURATION_SEC` | `60` | Максимум синхронного запроса |
| `VOICE_SEARCH_MODEL_NAME` / `VOICE_SEARCH_BEAM_SIZE` | `./models/whisper-small` / `1` | Локальный путь whisper small (качает model-downloader); compose уважает `.env`, без него — тот же пин³ |
| `VOICE_SEARCH_ENGINE` | `tone` | `tone` (дефолт, T-one) или `whisper` |
| `VOICE_SEARCH_MAX_CONCURRENCY` / `VOICE_SEARCH_QUEUE_WAIT_SEC` | `1` / `0.1` | Переполнение — `503` + `Retry-After` |
| `VOICE_SEARCH_USE_WORKER` / `VOICE_SEARCH_RESULT_TIMEOUT_SEC` | `true` / `2.5` | Отдельная очередь `worker-fast`; ожидание ответа⁴ |
| `VOICE_SEARCH_BEAM_MIN` | `2` | Нижняя граница beam (диагностика, не менять) |
| `VOICE_SEARCH_INITIAL_PROMPT` / `VOICE_SEARCH_HOTWORDS` | unset (встроенные) | Переопределение подсказок декодера; пустая строка = выкл; только для замеров |
| `TONE_MODEL_DIR` / `TONE_MODEL_FILE` / `TONE_THREADS` | `./models/t-one` / `model.onnx` / `1` | T-one; потоков — по числу физических ядер. `OMP_NUM_THREADS` — не сервисный env: compose выставляет его из `ASR_THREADS` (воркеры пинят 1) |
| `TONE_INTER_THREADS` / `TONE_GRAPH_OPT` / `TONE_DISABLE_ARENA` / `TONE_DISABLE_MEM_PATTERN` | `0` / `all` / `false` / `false` | Тонкие ручки onnxruntime (не менять) |
| `TONE_FUZZY_THRESHOLD` | `0.0` | Нечёткие термины выключены (эксперимент показал вред) |
| `TONE_FUZZY_OOV_ONLY` | `false` | Нечёткая замена только для слов вне словаря (в паре с порогом выше) |
| `TONE_DECODE` / `TONE_BEAM_WIDTH` / `TONE_HOTWORD_WEIGHT` | `greedy` / `10` / `3.0` | Декодер T-one (дефолт greedy) |
| `DIARIZATION_MIN_SPEAKER_DISTANCE` | `0.18` | Порог разделения спикеров |
| `LOCAL_LLM_URL` / `LOCAL_LLM_MODEL` | `http://llm-runtime:8080/v1` / `qwen2.5-7b-instruct` | Локальный LLM-рантайм (`local-full`) |
| `LLAMA_THREADS` | `4` | Потоки llama.cpp; на 8+ физических ядрах можно 6 |
| `LOCAL_LLM_JSON_MODE` | `json_schema` | Режим constrained decoding (`json_object` — слабее, только для замеров) |
| `LLM_EXTERNAL_PROVIDER_ENABLED` / `_URL` / `_API_KEY` / `_MODEL` | `false` / `""` / `""` / `gpt-4o-mini` | Внешний разбор только маскированного текста; по умолчанию выключен |
| `RETENTION_AUDIO_DAYS` / `RETENTION_TEXT_DAYS` | `14` / `365` | Аудио в MinIO / тексты в Postgres |
| `RATE_LIMIT_REQUESTS_PER_WINDOW` / `RATE_LIMIT_WINDOW_SEC` | `5` / `10` | Опрос статусов — не чаще 1 запроса в 3–5 с⁴ |
| `HF_TOKEN` | `""` | Только для приватных/gated-моделей |

¹ Кодовый дефолт `APP_ENV` — `development`; в примере стоит `production`, скрипт чистой машины ставит `development`.
² Пустой `DEV_TOKEN` в `.env` compose превращает в `dev-token` (подстановка `${DEV_TOKEN:-dev-token}`); кодовый дефолт тот же.
³ При движке `tone` whisper-small не грузится. ⁴ Без `.env` compose подставляет свои дефолты: `RATE_LIMIT_REQUESTS_PER_WINDOW=30`, `VOICE_SEARCH_RESULT_TIMEOUT_SEC=2.4`, `VOICE_SEARCH_BEAM_SIZE=2`, `ASR_THREADS=4`, `VOICE_SEARCH_MODEL_NAME=./models/whisper-small`; с `.env` из примера действуют значения примера. `LOCAL_LLM_URL`, `S3_*`, `REDIS_URL`, `DATABASE_URL` в коде по умолчанию указывают на localhost — в compose их перекрывают значения примера.

---

## API

Контракт — [`openapi.yaml`](openapi.yaml) (3.1). Все рабочие маршруты под `/v1/`; примеры на каждую функцию — в [`docs/acceptance-requests.md`](docs/acceptance-requests.md).

| Метод и ручка | Scope | Назначение |
|---|---|---|
| `POST /v1/jobs/upload` | `write` | Загрузка файла + метаданные (`namespace`, `external_id`, `direction`, `manager_id`, `client_id`, `callback_url`, `vocabulary_hints`); `Idempotency-Key`, дедуп по хэшу |
| `POST /v1/jobs/by-url` | `write` | Задание по ссылке (SSRF-защита, скачивание в фоне → `202`) |
| `POST /v1/jobs/by-s3` | `write` | Задание по объекту в S3/MinIO |
| `GET /v1/jobs/{job_id}` | `read` | Статус (`queued`→`running`→`succeeded`/`failed`), прогресс, `result_url` |
| `GET /v1/calls/{call_id}/transcript` | `read` (`masked=false` — `admin`) | `json`/`text`/`srt`/`vtt`, `masked=true` по умолчанию |
| `GET /v1/calls/{call_id}/analysis` | `read` | Разбор по JSON-схеме (версии схемы и промпта в ответе) |
| `GET /v1/calls/{call_id}/audio` | `read` | Оригинальное аудио |
| `GET /v1/calls` | `read` | Поиск (`q`, фильтры) + курсорная пагинация (`cursor`/`next_cursor`, кап 100) |
| `POST /v1/voice/search` | `write` | Синхронный голосовой поиск; аудио только в памяти, не сохраняется |
| `GET /v1/voice/history` | `read` | История запросов namespace (маскированный текст) |
| `DELETE /v1/voice/history` | `write` | Очистка истории (`{"status":"cleared",...}`) |
| `GET /v1/voice/sample-audio` | — | Синтетический wav для проверки тракта |
| `GET /v1/analytics/summary` | `read` | Сводка: темы, топ сущностей, чек-лист, тренды по неделям |
| `POST /v1/texts/analyze` | `write` | Кластеризация пачки текстов (TF-IDF + HDBSCAN/KMeans) |
| `GET /v1/analytics/export_csv` (`/v1/analytics/export.csv`) | `read` | Экспорт CSV |
| `DELETE /v1/data/delete` | `admin` | Удаление одной ручкой: `call_id` / `participant_id` / период |
| `POST /v1/integrations/bitrix24/webhook` | `write` | Вебхук телефонии Битрикс24 |
| `POST /v1/admin/tokens/revoke` / `GET /v1/admin/tokens/revoked` | `admin` | Горячий отзыв токенов через Redis |
| `GET /health` / `/ready` / `/metrics` | — | Живость / готовность (БД, Redis, S3, прогрев воркеров) / Prometheus |
| `GET /dashboard` | — | Страница звонков: список с фильтрами, плеер, расшифровка, разбор (вход по токену, см. «Дашборд») |
| `GET /voice` | — | Страница голосового поиска: запрос, история, кластеры, сводка (вход по токену) |

Ошибки — единым конвертом `{"error": "slug", "message": "по-русски", "details": {}}`; коды `400/401/403/404/409/413/422/429/5xx`, у `429` и `503 voice_search_timeout` — заголовок `Retry-After`. Заголовок `X-Request-Id` принимается и возвращается всегда.

Аутентификация: `Authorization: Bearer <токен>`. При `APP_ENV != production` токен сверяется с `DEV_TOKEN` как есть (полный доступ, без namespace). При `APP_ENV=production` — только `API_TOKENS_HASHED`, запись `sha256:scope1+scope2:namespace` (namespace можно опустить), через запятую; чужой namespace — `403`. Выпуск: `TOKEN=$(python3 -c "import secrets; print(secrets.token_hex(32))")`, в `.env` — его sha256, клиенту — исходный токен. Отзыв — ручкой revoke без перезапуска.

Callback: при `callback_url` результат уходит `POST` с заголовками `X-Apc-Timestamp` (unix-секунды) и `X-Apc-Signature: sha256=HMAC("<timestamp>.<body>", CALLBACK_SIGNING_SECRET)`; приёмник старше 5 минут отклоняет; повторы — 5 попыток с задержками 5/15/60/300/900 с, лог — в `callback_logs`; редиректы и приватные сети для доставки блокируются.

---

## Качество и скорость

Стенд: AMD Ryzen 7 8845HS, governor `performance`; `small` — cpuset 2 ядра, `local-full` — 8 vCPU. Все цифры — только из запусков скриптов репозитория, сырые данные — в `reports/`. Ориентиры — из раздела «Качество и скорость» `docs/brief.md`. Подробности, методика и оговорки — в [`docs/eval-report.md`](docs/eval-report.md).

| Что мерили | Ориентир ТЗ | Замер | Итог |
|---|---|---|---|
| Голос 10 с → текст, 2 ядра (T-one) | ≤ 2,5 с | 400 запросов: 0 × 503, p95 2,00 с (без капа частоты, ~4,29 ГГц); при капе 3,0 ГГц p95 2,41 с; чистая машина E6 — 200 за ~1,2 с | OK, запас зависит от частоты и AVX2 |
| Час аудио, `small` 2 ядра | ≤ 60 мин (RTF ≤ 1,0) | 3431 с / RTF 0,953 без капа; 5028 с / RTF 1,397 с капом 3,0 ГГц; beam=1 — 4808 с / RTF 1,336; пик RAM 1997,7 МиБ при лимите 2112 | OK впритык без капа; с капом — мимо |
| Час аудио, `local-full` 8 vCPU | в 2 раза быстрее | 3456 с / RTF 0,960 — выигрыша нет (ASR не масштабируется ядрами) | мимо |
| WER чистая речь | ≤ 12 % | Golos-100: podlodka 18,4 %, T-one 16,1 %; RuLS-296: 9,8 % / 6,0 %; SOVA-265: 13,7 % / 13,0 % | мимо на Golos (команды + wake-слова) и SOVA-clean; OK на RuLS |
| WER телефония 8 кГц | ≤ 25 % | Golos-100: podlodka 35,2 %, T-one 19,6 %; RuLS-296: 10,5 % / 7,9 %; SOVA-265: 15,9 % / 11,6 % | OK везде, кроме podlodka на Golos |
| Термины с подсказками | ≥ 90 % | whisper-small: 63,6 % с подсказками (61,4 % без); точечная поправка split A/B — 86,4 %, в прод не вшита (ADR-020) | мимо |
| DER + роли (набор DER-30) | ориентира нет | моно 0,163 / роли 0,94; стерео 0,286 / роли 0,98 | измерено |
| Разбор звонка Qwen | ≤ 30 с, JSON всегда | 8 физических ядер (`-t 6`): медиана 25,8 с; 2 ядра: медиана 65,4 с | OK на 8 ядрах; на 2 — только GPU (не измерено, GPU на стенде нет) |
| Типы / сущности (A120+B25) | ориентира нет | типы: rules 0,60/0,53, Qwen 0,80/0,67 (acc/macro-F1 на B); ASR-шум −0,08…−0,12 | измерено |
| Common Voice ru | открытые наборы | не измерено — датасет пуст на Hub | не измерено |
| Стоимость внешнего провайдера | расчёт | 802 + 235 токенов на разбор; фактических вызовов не было | расчёт, не измерено |

Расхождения с ориентирами названы прямо: час с капом частоты, ускорение ×2 на 8 vCPU, WER чистой на Golos/SOVA, термины 90 % — не достигнуты; причины (домен наборов, частота, немасштабируемый ASR) — в eval-report, данные не подгонялись. Наборы A/B, DER-30 и часовка — синтетика (gTTS), оценка оптимистична.

---

## Дашборд

Две страницы без сборки, отдаёт сам `api` из `app/static/` (общие модули `js/api.js`,
`js/escape.js`, `js/format.js`):

- `/dashboard` (также `/`, `/ui`) — звонки: журнал с фильтрами (текст, менеджер,
  тип обращения, период) и курсорной пагинацией; карточка звонка — собственный
  WebAudio-плеер (аудио грузится через `fetch` с `Authorization` и декодируется
  `decodeAudioData`, waveform на canvas, клик — перемотка), пословная подсветка
  и подсветка текущей реплики, клик по реплике перематывает плеер, говорящие и роли;
  длинные записи (дольше 10 мин или больше 32 МБ) воспроизводятся через
  MEDIA-элемент с blob-URL без декодирования в память — файл при этом
  загружается целиком в blob (лимит 300 МБ), Range-запросы без изменения API
  невозможны; подсветка и seek те же;
  разбор по схеме (тип, результат, сущности, договорённости, чек-лист, метрики речи,
  эмоции, версии схемы и промпта).
- `/voice` — голосовой поиск (запись до 60 с / файл; запись с микрофона
  конвертируется в браузере в WAV PCM16 16 кГц моно — того требует быстрый
  путь распознавания), история запросов, кластеризация
  «о чём спрашивают» (источник — история запросов или тексты первых 20 звонков журнала),
  сводка (`GET /v1/analytics/summary`: темы, топ сущностей, чек-лист, тренды по неделям)
  и выгрузка CSV (`GET /v1/analytics/export_csv`).
- `/dashboard-classic`, `/ui-classic` — 303-редирект на `/dashboard`.

Вход: при первом открытии страница просит неймспейс и Bearer-токен (минимум `read`;
загрузка, голосовой поиск, кластеризация и очистка истории требуют `write`). Токен хранится
только в `sessionStorage` вкладки, в коде и разметке его нет. `401/403` и обрывы сети
показываются явным баннером с кнопкой «Ввести токен», а не глотаются.

Офлайн и безопасность страниц: внешние CDN отсутствуют — Bootstrap 5.3.2 (MIT)
и Bootstrap Icons 1.11.3 (MIT) лежат в `app/static/vendor/` (подробно — ADR-024).
Плеер — собственный WebAudio (`js/player.js`, WaveSurfer удалён как ненадёжный —
см. ADR-025). Заголовки страниц: строгий CSP (`default/script/style/connect/media-src
'self'`, из исключений только `img-src data:` для SVG-стрелок Bootstrap),
`X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`.

Ограничения (честно): аудио больше 300 МБ во вкладку не грузится (показываются
расшифровка и разбор + подсказка забрать файл через API); голосовой поиск на CPU без
прогретого `worker-fast` отвечает `503` (см. `known-issues` п.6) — страница показывает
«временно недоступен, повторите через N с» с `Retry-After`, а не ошибку распознавания;
фильтр по категории товара отсутствует — для него нужен необязательный параметр
`category` у `GET /v1/calls` (обратно совместимое расширение API, в этом проходе
не делалось: API frozen).

| Ограничение | Суть | Статус / обход |
|---|---|---|
| Фильтр по категории товара | нужен необязательный параметр `category` у `GET /v1/calls` (расширение API + `openapi.yaml`) | не делалось, API frozen |
| Микрофон | запись с микрофона (`getUserMedia`) работает только на localhost/HTTPS | вне localhost/HTTPS — только загрузка файла |
| Проигрывание больше 300 МБ | файл грузится целиком в blob, Range-запросы без изменения API невозможны | остаются расшифровка и разбор + подсказка забрать файл через API |
| Safari / Firefox / мобильные | проверен только Chromium desktop; Safari/Firefox и мобильные вьюпорты после изменений не проверены | внутренний инструмент, Chromium — целевой |
| Звук «ушами» | headless без аудиовыхода: проверены часы/подсветка/DOM по CDP, звук и реальный микрофон — нет | — |
| `make live-check` | живой CDP-прогон, не входит в `make check` и CI (нужен стенд с моделями и браузер) | без сервера/браузера/токена — SKIP, не провал |

Живая проверка в настоящем Chromium (CDP, без Playwright-зависимостей):

```bash
DASHBOARD_TOKEN=<токен с read+write+admin> make live-check
```

Скрипт `scripts/dashboard_live_check.mjs` сам поднимает headless-Chromium
с фейковым микрофоном, загружает эталонный звонок в изолированный неймспейс
(`DASHBOARD_NAMESPACE`, дефолт `dashboard_live`), ждёт задание и проходит:
вход по токену, журнал, карточку, проигрывание с перемоткой и подсветкой,
разбор и состояние «разбор отсутствует», сводку, кластеры, голосовой поиск —
и падает при любом `securitypolicyviolation`, `console.error` или необработанном
исключении. Скриншоты — в `reports/dashboard-audit/live_*.png`. Переменные:
`DASHBOARD_BASE`, `CDP_PORT`, `CHROMIUM_BIN`, `FAKE_WAV`, `FIXTURE_WAV`, `OUT_DIR`.
В `make check` и CI не входит (там нет живого стенда с моделями и браузера);
без сервера/браузера/токена завершается SKIP, а не провалом.

---

## Безопасность и данные

- PII: телефоны, e-mail, карты, паспорта маскируются до сохранения транскрипта, до истории голосового поиска и до отправки во внешний провайдер (перед отправкой — `assert_no_leaked_pii`); в логи и метрики расшифровки и ПДн не пишутся (guard в `make check`).
- Ретеншн: аудио в MinIO — `RETENTION_AUDIO_DAYS` (14), тексты в Postgres — `RETENTION_TEXT_DAYS` (365); чистит `cleanup-retention`; срочное удаление — `DELETE /v1/data/delete`.
- Изоляция: всё режется по `namespace`; скоуп-токены чужого namespace получают `403`.
- SSRF: загрузка по ссылке и доставка callback блокируют приватные/loopback/link-local/metadata-адреса, лимиты редиректов и размера, таймауты; входные файлы считаются враждебными (ffprobe-валидация, лимиты, ffmpeg только массивом аргументов).
- Сети compose: `worker-long`/`worker-fast`/`llm-runtime` — в isolated `backend` без egress (проверено тестом); скачивание по URL делает `api`.
- Ротация без простоя: токены — перекрытием + мгновенный отзыв; секрет callback — двойным приёмом на платформе; пароли БД/MinIO — с простоем. Процедура — в [`docs/token-rotation.md`](docs/token-rotation.md).

---

## Эксплуатация

- [`docs/runbook.md`](docs/runbook.md) — запуск/остановка, мониторинг (`/health`, `/ready`, `/metrics`), разбор инцидентов («что делать, если...»), логи, MinIO.
- [`docs/update-guide.md`](docs/update-guide.md) — обновления образов/моделей, миграции, бэкап и откат.
- [`docs/token-rotation.md`](docs/token-rotation.md) — ротация токенов и секретов.
- [`docs/known-issues.md`](docs/known-issues.md) — ограничения, технический долг, цифры стендовых провалов.
- [`docs/decisions.md`](docs/decisions.md) — архитектурные решения (ADR).
- [`docs/acceptance-requests.md`](docs/acceptance-requests.md) — приёмочные curl на каждую функцию.
- [`docs/eval-report.md`](docs/eval-report.md) — отчёт оценочного стенда.

---

## Лицензии третьих сторон

| Компонент | Лицензия | Как используется | Замена / примечание |
|---|---|---|---|
| MinIO (образ `elestio/minio:latest@sha256:25348a…c556d4bd`) | AGPLv3 | Отдельный немодифицированный контейнер; код обращается только через стандартный S3-клиент `boto3` | Заменяется любым S3-совместимым хранилищем через `S3_ENDPOINT_URL`/ключи/бакеты в `.env` (см. ADR-023); поставка — после акцепта AGPL заказчиком либо с заменой |
| navec 0.10.0 (код + веса) | MIT | Библиотека + веса (бандл колеса `natasha`) для NER | Допущено в закрытый продукт |
| slovnet 0.6.0 (код) | MIT (проверено по файлам пакета: `LICENSE`, `METADATA`, репозиторий `natasha/slovnet`) | Библиотека для NER | Код допущен |
| slovnet-веса (`slovnet_ner_news_v1.tar`) | Явного гранта не найдено — вопрос открыт | Веса (бандл колеса `natasha`, внутри tar файла лицензии нет) | Требуется подтверждение автора/юриста; не закрывать по аналогии с navec |
| CAM++ (`models/campplus.onnx`, `sha256:dd1740aa…a2a7f`, 28 283 928 байт) | Apache 2.0 | Веса (источники — `CAMPPLUS_URLS` в `scripts/download_models.py`), инференс через `onnxruntime` | Допущено; при смене весов лицензию и хэш сверять заново |
| faster-whisper / CTranslate2 / onnxruntime, whisper-подлодка (Apache 2.0), Qwen2.5-7B (Apache 2.0), llama.cpp (MIT), Redis 7.2 (BSD-3-Clause), PostgreSQL/pgvector (PostgreSQL Licence) | См. `docs/decisions.md` ADR-012 | Библиотеки / веса / образы | Полный разбор — в ADR-012 и ADR-023 |
| Bootstrap 5.3.2 (`app/static/vendor/bootstrap-5.3.2/`) | MIT | CSS+JS дашборда, вендор без CDN | Обновление вручную фиксированной версией (ADR-024) |
| Bootstrap Icons 1.11.3 (`app/static/vendor/bootstrap-icons-1.11.3/`) | MIT | Иконки и шрифты дашборда, вендор без CDN | Обновление вручную фиксированной версией (ADR-024) |

WaveSurfer.js 7.12.12 (BSD-3-Clause) удалён из вендора 2026-10-15 (см. ADR-025:
плеер заменён собственным WebAudio `js/player.js`).

Полный разбор лицензий — в [`docs/decisions.md`](docs/decisions.md) (ADR-012, ADR-023).

---

## Тесты и проверки

```bash
make check   # линтер + форматтер + guard-скрипты + проверка OpenAPI + тесты + gitleaks
```

`make check` включает: `ruff check`/`format --check`, guard-скрипты (запрет внешних ASR, PII в логах, shell-вызовы, сети воркеров, пины образов), сверку `openapi.yaml` с кодом, полный `pytest` (актуальное число — вывод команды и CI; счётчик здесь не дублируется) и `gitleaks` по истории. Оценочный стенд: `evaluation/` + `scripts/eval_*.py`, `scripts/bench_*.py`, `scripts/sweep_long.py` — отчёт в `docs/eval-report.md`, сырые цифры в `reports/`. Проверка чистой машины: `bash scripts/clean_machine_check.sh` — требует чистое git-дерево, ≥ 15 ГБ на диске, свободный порт (`CLEAN_CHECK_HOST_PORT`, дефолт 8001); клонирует HEAD во временную папку, ставит только по README + `.env.example`, гоняет приёмку и пишет отчёт в `reports/clean_machine_<дата>.md`.
