# Приёмочные запросы (curl) по функциям из `docs/brief.md`

Подготовка (стенд из README: `cp .env.example .env`, `APP_ENV=development`,
`DEV_TOKEN` задан, `docker compose up` поднят):

```bash
BASE=http://localhost:8000
TOKEN=my-dev-secret-123
NS=outdoor_msk
```

Все ответы — единый конверт ошибок `{error, message, details}` при сбоях.
Ниже для каждой функции brief — запрос и что проверить в ответе.

## 0. Живость и готовность

```bash
curl -s $BASE/health | jq .
# {"status":"ok",...} — 200

curl -s $BASE/ready -H "Authorization: Bearer $TOKEN" | jq .
# {"status":"ready","dependencies":{"database":true,"redis":true,"s3":true}} — 200
# в первые минуты после старта допустим 503 (прогрев моделей), см. runbook

curl -s $BASE/metrics | head -20
# prometheus-метрики — 200
```

## 1. Приём аудио (brief п.1)

```bash
# тестовый файл без внешних данных:
curl -s $BASE/v1/voice/sample-audio -H "Authorization: Bearer $TOKEN" -o /tmp/opencode/sample.wav
file /tmp/opencode/sample.wav

# 1а. загрузка файла с метаданными:
curl -s -X POST "$BASE/v1/jobs/upload" -H "Authorization: Bearer $TOKEN" \
  -F "namespace=$NS" -F "file=@/tmp/opencode/sample.wav" \
  -F "external_id=acc-001" -F "direction=inbound" \
  -F "manager_id=mgr-1" -F "client_id=cli-1" | jq .
# 202, есть job_id и status_url

# 1б. идемпотентность: повтор с тем же Idempotency-Key — то же задание:
curl -s -X POST "$BASE/v1/jobs/upload" -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: acc-001" \
  -F "namespace=$NS" -F "file=@/tmp/opencode/sample.wav" | jq .job_id

# 1в. задание по ссылке (URL должен быть публичным, действует SSRF-защита):
curl -s -X POST "$BASE/v1/jobs/by-url" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"namespace\":\"$NS\",\"url\":\"https://example.com/call.wav\"}" | jq .

# 1г. задание по объекту S3/MinIO (бакет raw-audio создаётся compose):
curl -s -X POST "$BASE/v1/jobs/by-s3" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"namespace\":\"$NS\",\"s3_key\":\"acc-003.wav\",\"s3_bucket\":\"raw-audio\"}" | jq .
# для существующего ключа — 202; для отсутствующего — 4xx-конверт
```

## 2. Распознавание (brief п.2) — через статус задания

```bash
JOB=<job_id_из_п1а>
for i in $(seq 1 40); do
  S=$(curl -s "$BASE/v1/jobs/$JOB?namespace=$NS" -H "Authorization: Bearer $TOKEN" | jq -r .status)
  echo "$S"; [ "$S" = "succeeded" ] && break; sleep 5
done
curl -s "$BASE/v1/jobs/$JOB?namespace=$NS" -H "Authorization: Bearer $TOKEN" | jq .
# status=succeeded, progress=1.0; сегменты — в транскрипте ниже
```

## 3. Говорящие (brief п.3)

```bash
CALL=$(curl -s "$BASE/v1/jobs/$JOB?namespace=$NS" -H "Authorization: Bearer $TOKEN" | jq -r .call_id)
curl -s "$BASE/v1/calls/$CALL/transcript?namespace=$NS&format=json" \
  -H "Authorization: Bearer $TOKEN" | jq '.segments[0] | {speaker_label, speaker_role, start, end, text}'
# есть speaker_label/speaker_role (manager/client), start/end — числа
```

## 4. Короткий синхронный запрос (brief п.4)

```bash
time curl -s -X POST "$BASE/v1/voice/search?namespace=$NS&language=ru" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@/tmp/opencode/sample.wav;type=audio/wav" | jq .
# 200 и поле text; аудио нигде не сохраняется (повторный GET истории — только маскированный текст)
# перегрузка/таймаут — 503 voice_search_timeout + заголовок Retry-After
```

## 5. Разбор по JSON-схеме (brief п.5)

```bash
curl -s "$BASE/v1/calls/$CALL/analysis?namespace=$NS" \
  -H "Authorization: Bearer $TOKEN" | jq '{schema_version, prompt_version, inquiry_type, call_result}'
# schema_version и prompt_version присутствуют; inquiry_type — из config/taxonomy.yaml
curl -s "$BASE/v1/calls/$CALL/analysis?namespace=$NS" \
  -H "Authorization: Bearer $TOKEN" | jq '.entities, .manager_checklist, .deterministic_speech_metrics'
```

## 6. Сводная аналитика и тексты (brief п.6)

```bash
curl -s "$BASE/v1/analytics/summary?namespace=$NS&date_from=2026-09-01T00:00:00Z&date_to=2026-10-13T00:00:00Z" \
  -H "Authorization: Bearer $TOKEN" | jq .
curl -s -X POST "$BASE/v1/texts/analyze" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"namespace":"demo_outdoor_msk","texts":["щиты 3х6 в Балашихе на октябрь","узнать прайс на суперсайт"]}' | jq .
curl -s "$BASE/v1/analytics/export_csv?namespace=$NS&date_from=2026-09-01T00:00:00Z" \
  -H "Authorization: Bearer $TOKEN" | head -5
# CSV с заголовком
```

## 7. Персональные данные (brief п.7)

```bash
# маскирование видно в транскрипте/истории (телефоны → [ТЕЛЕФОН] и т.п.)
curl -s "$BASE/v1/voice/history?namespace=$NS" -H "Authorization: Bearer $TOKEN" | jq .

# удаление одной ручкой: запись / участник / период
curl -s -X DELETE "$BASE/v1/data/delete?namespace=$NS&call_id=$CALL" \
  -H "Authorization: Bearer $TOKEN" | jq .
curl -s -X DELETE "$BASE/v1/data/delete?namespace=$NS&participant_id=cli-1" \
  -H "Authorization: Bearer $TOKEN" | jq .
curl -s -X DELETE "$BASE/v1/data/delete?namespace=$NS&date_from=2026-01-01T00:00:00Z&date_to=2026-12-31T00:00:00Z" \
  -H "Authorization: Bearer $TOKEN" | jq .
```

## 8. Хранение и поиск (brief п.8)

```bash
for F in json text srt vtt; do
  echo "== $F"; curl -s "$BASE/v1/calls/$CALL/transcript?namespace=$NS&format=$F" \
    -H "Authorization: Bearer $TOKEN" | head -c 300; echo
done
curl -s "$BASE/v1/calls?namespace=$NS&limit=5" -H "Authorization: Bearer $TOKEN" | jq .
curl -s "$BASE/v1/calls?namespace=$NS&q=щит&limit=5" -H "Authorization: Bearer $TOKEN" | jq .
# курсорная пагинация: next_cursor → ?cursor=...; чужой namespace — 403
```

## 9. Задания и callback'и (brief п.9)

```bash
# статусы и прогресс — п.2 выше; подпись callback проверяется приёмником:
# заголовки X-Apc-Timestamp / X-Apc-Signature: sha256=HMAC("<timestamp>.<body>")
# ретраи: 5 попыток с задержками 5/15/60/300/900 с (см. код callback_dispatcher.py)
# задание с callback (приёмник должен быть публичным URL):
curl -s -X POST "$BASE/v1/jobs/upload" -H "Authorization: Bearer $TOKEN" \
  -F "namespace=$NS" -F "file=@/tmp/opencode/sample.wav" \
  -F "callback_url=https://example.com/hook" | jq .
```

## 10. Желательное: дашборд, Битрикс24, S3-инбокс

```bash
# дашборд:
curl -s -o /dev/null -w "%{http_code}\n" $BASE/dashboard
# вебхук Битрикс24 (нужны namespace и RECORD_URL записи; scope write):
curl -s -X POST "$BASE/v1/integrations/bitrix24/webhook?namespace=$NS&token=$TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"event":"ONVOXIMPLANTCALLFINISHED","data":{"RECORD_URL":"https://example.com/rec.mp3","CALL_ID":"crm-1"}}' | jq .
# без RECORD_URL — 400 missing_record_url (тоже проверка)
```
