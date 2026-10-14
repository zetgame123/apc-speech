# Обновление образов, моделей, схемы БД и откат

Все команды — из корня репозитория. Перед любым обновлением зафиксируйте
исходное состояние: `git rev-parse HEAD`, `docker compose images`, дамп БД
(п.4).

## 1. Обновление образов

Пины зафиксированы в `docker-compose.yml` (тест `tests/test_images_pinned.py`
падает на плавающих тегах — это защита). Порядок:

```bash
git pull                                    # или checkout нужного тега
grep -E "image:" docker-compose.yml        # сверить пины
docker compose pull                         # для registry-образов
APP_PROFILE=small docker compose up -d --build
docker compose ps                           # все healthy
curl -s http://localhost:8000/health | jq .
curl -s http://localhost:8000/ready -H "Authorization: Bearer <токен>"
```

`local-full`: добавьте `--profile local-full` к `ps`/`up` и дождитесь
`llm-runtime` (прогрев модели ~минута).

Откат образов: предыдущие digest'ы — в `git log -p -- docker-compose.yml`;
верните файл, `up -d --build`, проверьте `/health`/`/ready`.

## 2. Обновление моделей

Загрузка идемпотентна (существующие файлы не перекачиваются):

```bash
uv run python scripts/download_models.py --profile small        # CAM++, whisper-small, podlodka-turbo-ct2
uv run python scripts/download_models.py --profile local-full  # + Qwen2.5-7B GGUF (~4,4 ГБ)
```

В compose скачивание делает одноразовый `model-downloader`
(`model-downloader-llm` для Qwen) перед стартом `api`/воркеров — ручной запуск
нужен только для прогрева кэша. После подмены весов пересоздайте потребителей:

```bash
docker compose up -d --force-recreate worker-long worker-fast api
```

Проверьте: голосовой запрос (`POST /v1/voice/search`) и одно длинное задание
до `succeeded`. Откат моделей: предыдущие веса храните копией каталога
`./models/` (в git веса не входят); верните файлы, пересоздайте те же сервисы.
Новая модель с другой лицензией — только после записи в `docs/decisions.md`
(правило AGENTS.md п.5).

## 3. Обновление схемы БД

Миграции — Alembic (`alembic/versions/`), при `up` их применяет одноразовый
сервис `alembic-migrate` перед стартом `api`. Вручную:

```bash
docker compose up alembic-migrate   # применить
docker compose logs alembic-migrate # сверить applied
```

Правила: новая миграция в том же релизе, что и код, который её требует;
`downgrade` обязан существовать и быть проверенным (см. п.4). Проверка:
`GET /v1/calls` и создание задания после миграции.

## 4. Бэкап перед обновлением и откат

```bash
docker compose exec postgres pg_dump -U apc_user apc_speech > /tmp/opencode/apc_backup_$(date +%F).sql
git rev-parse HEAD > /tmp/opencode/apc_backup_version.txt
docker compose images > /tmp/opencode/apc_backup_images.txt
```

Откат релиза целиком:

```bash
git checkout <предыдущий_коммит_или_тег>
docker compose up -d --build
docker compose up alembic-migrate   # если релиз менял схему — откатить downgrade до ревизии релиза:
#   docker compose run --rm api alembic downgrade <ревизия>
```

Порядок при откате со сменой схемы: сначала код предыдущего релиза, затем
`downgrade`, затем проверка `/ready` и сквозного задания. Данные, созданные
новым кодом по новой схеме, downgrade может не перенести — это окно
фиксируйте простоем приёма заданий (`MAX_PENDING_JOBS` не поможет —Bins).
Честно: бесшовного отката схемы без потери новых записей нет; бэкап — обязателен.

## 5. Что проверить после любого обновления

1. `docker compose ps` — все healthy.
2. `/health` — 200; `/ready` — 200 (в первые минуты после старта допустим 503,
   см. runbook).
3. Сквозное задание upload → `succeeded` → транскрипт → разбор.
4. `POST /v1/voice/search` — 200 в пределах таймаута.
5. `docker compose logs --tail=50 api worker-long worker-fast` — без Traceback.
