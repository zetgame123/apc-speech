# Ротация API-токенов и секрета подписи callback без простоя

Все команды — из корня репозитория. После любого изменения `.env` новые значения
подхватывают только **пересозданные** контейнеры (`settings` кэшируются на старте
процесса, см. `get_settings()` в `app/core/config.py`); простой `restart` старые
переменные не подхватит.

## 1. Формат токенов

`API_TOKENS_HASHED` — список через запятую, запись:
`sha256_токена:scope1+scope2:namespace` (`namespace` можно опустить).
На сервере хранится только хэш, исходный токен — только у клиента
(`Authorization: Bearer <исходный_токен>`). Области: `admin`, `write`, `read`.

Выпуск нового токена:

```bash
TOKEN=$(python3 -c "import secrets; print(secrets.token_hex(32))")
echo "Выдать клиенту: $TOKEN"
HASH=$(python3 -c "import hashlib; print(hashlib.sha256('$TOKEN'.encode()).hexdigest())")
echo "В .env: $HASH"
```

## 2. Ротация API-токена (перекрытие, без простоя обработки)

```bash
BASE=http://localhost:8000
NS=outdoor_msk
```

1. Добавьте хэш нового токена **рядом со старым** в `.env`:
   ```env
   API_TOKENS_HASHED="<хеш_старого>:admin+write+read:outdoor_msk,<хеш_нового>:admin+write+read:outdoor_msk"
   ```
2. Пересоздайте только `api` (воркеры продолжают обрабатывать очередь):
   ```bash
   docker compose up -d --force-recreate api
   ```
   Простой — только секунды перезапуска `api`; задания в Redis не теряются,
   `worker-long`/`worker-callback` их продолжают. Синхронные `voice/search`,
   попавшие в окно рестарта, получат ошибку соединения — повторите их.
   Окно планируйте на низкую нагрузку.
3. Проверьте оба токена запросом, требующим авторизации
   (`/ready` публичный и для этого не годится):
   ```bash
   curl -s -o /dev/null -w "old:%{http_code}\n" \
     "$BASE/v1/calls?namespace=$NS&limit=1" -H "Authorization: Bearer <старый_токен>"
   curl -s -o /dev/null -w "new:%{http_code}\n" \
     "$BASE/v1/calls?namespace=$NS&limit=1" -H "Authorization: Bearer $TOKEN"
   ```
   (оба — 200; отозванный/чужой — 401/403).
4. Переключите клиентов на новый токен постепенно.
5. Отзовите старый **мгновенно, без перезапуска**:
   ```bash
   curl -s -X POST http://localhost:8000/v1/admin/tokens/revoke \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d "{\"token_hash\": \"<хеш_старого>\"}"
   ```
   Отзыв расходится по всем процессам через Redis-множество `revoked_tokens`.
   Проверьте: `GET /v1/admin/tokens/revoked` — хэш в списке; запросы со старым
   токеном — `401`.
6. Уберите старый хэш из `.env` и пересоздайте `api` ещё раз (заодно с п.2
   следующего окна). Проверьте, что старый токен даёт `401`, новый — `200`.

Откат: пока старый хэш не удалён из `.env` и не отозван — просто верните
клиентов на старый токен.

## 3. Ротация секрета подписи callback (`CALLBACK_SIGNING_SECRET`)

Код держит **один** активный секрет (`sign_callback_payload()` в
`app/core/security.py` считает `HMAC-SHA256` от `"<timestamp>.<body>"`,
заголовки `X-Apc-Timestamp` / `X-Apc-Signature: sha256=...`). Перекрытие
делается **на стороне приёмника** (платформы): она должна временно принимать
оба секрета.

1. Сгенерируйте новый секрет (минимум 32 символа):
   ```bash
   NEW_SECRET=$(openssl rand -base64 32); echo "$NEW_SECRET"
   ```
2. На стороне платформы включите приём подписей и по старому, и по новому
   секрету (сверка `hmac.compare_digest`, допуск рассинхрона часов ±5 мин
   по `X-Apc-Timestamp`).
3. Замените `CALLBACK_SIGNING_SECRET` в `.env`, пересоздайте отправителей:
   ```bash
   docker compose up -d --force-recreate api worker-callback
   ```
   (`api` подписывает синхронные ответы-заготовки, `worker-callback` —
   ретраи доставки; недоставленные callback'и лежат в Redis и уйдут с новой
   подписью после рестарта.)
4. Проверьте подпись тестовой доставкой: создайте задание с `callback_url`
   приёмника и сверьте подпись на его стороне:
   ```bash
   python3 -c "import hashlib,hmac; print(hmac.new(b'<new>', f\"{ts}.\".encode()+body, hashlib.sha256).hexdigest())"
   ```
   либо примите один реальный callback и сравните заголовок.
5. Отключите старый секрет на платформе. В production старт с секретом короче
   32 символов или со значением по умолчанию роняет сервис сознательно
   (`app/main.py`) — это защита, а не сбой.

Откат: верните старый секрет в `.env`, пересоздайте те же два сервиса,
отключите новый секрет на платформе.

## 4. Смена паролей БД и MinIO

`DB_PASSWORD` используется и в `DATABASE_URL`, `MINIO_ROOT_PASSWORD` — и в
`S3_SECRET_KEY`: меняйте пары согласованно, затем
`docker compose up -d --force-recreate` затронутых сервисов. Это **с простоем**:
сначала остановите запись (`api`), смените пароль в СУБД/ MinIO, обновите
`.env`, поднимите стек. Планируйте окном.
