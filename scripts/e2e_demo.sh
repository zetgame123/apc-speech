#!/usr/bin/env bash
set -e

API_URL="${API_BASE_URL:-http://localhost:8000}"
TOKEN="${DEV_TOKEN:-dev-token}"
NAMESPACE="demo_outdoor_msk"

echo "================================================================="
echo "  APC-SPEECH END-TO-END DEMO WALKTHROUGH"
echo "  Target API: ${API_URL}"
echo "================================================================="

# 1. Healthcheck & Readiness
echo -e "\n[Шаг 1] Проверка жизнеспособности и готовности сервиса..."
curl -s -f "${API_URL}/health" | grep -q "ok" && echo "  ✓ /health: OK" || (echo "  ✗ Ошибка /health" && exit 1)
curl -s -f "${API_URL}/ready" | grep -q "ready" && echo "  ✓ /ready: Все зависимости доступны" || (echo "  ✗ Ошибка /ready" && exit 1)

# Ensure sample audio exists
AUDIO_FILE="data/evaluation_suite/sample_dialogue_01_telephony_8k.wav"
if [ ! -f "${AUDIO_FILE}" ]; then
  echo "  Генерация тестового телефонного аудиофайла..."
  python3 -m evaluation.dataset_generator > /dev/null 2>&1 || uv run python -m evaluation.dataset_generator > /dev/null 2>&1
fi

# 2. Fast-Track Voice Search
echo -e "\n[Шаг 2] Синхронный голосовой поиск (SLA <= 2.5 с)..."
START_TIME=$(date +%s%N)
SEARCH_RESP=$(curl -s -X POST "${API_URL}/v1/voice/search?namespace=${NAMESPACE}" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: audio/wav" \
  --data-binary "@${AUDIO_FILE}")
END_TIME=$(date +%s%N)
ELAPSED_MS=$(( (END_TIME - START_TIME) / 1000000 ))

echo "  ✓ Ответ Voice Search получен за ${ELAPSED_MS} мс:"
echo "    ${SEARCH_RESP}"

# 3. Create Asynchronous Analysis Job
echo -e "\n[Шаг 3] Загрузка телефонного звонка на полный анализ..."
JOB_RESP=$(curl -s -X POST "${API_URL}/v1/jobs/upload" \
  -H "Authorization: Bearer ${TOKEN}" \
  -F "namespace=${NAMESPACE}" \
  -F "file=@${AUDIO_FILE}" \
  -F "external_id=demo_call_999" \
  -F "direction=inbound")

JOB_ID=$(echo "${JOB_RESP}" | grep -o '"job_id":"[^"]*' | cut -d'"' -f4)
echo "  ✓ Задание создано: Job ID = ${JOB_ID}"

# 4. Poll Job Status
echo -e "\n[Шаг 4] Опрос статуса задания до завершения..."
STATUS="queued"
for i in {1..20}; do
  STATUS_RESP=$(curl -s -X GET "${API_URL}/v1/jobs/${JOB_ID}?namespace=${NAMESPACE}" \
    -H "Authorization: Bearer ${TOKEN}")
  STATUS=$(echo "${STATUS_RESP}" | grep -o '"status":"[^"]*' | cut -d'"' -f4)
  CALL_ID=$(echo "${STATUS_RESP}" | grep -o '"call_id":"[^"]*' | cut -d'"' -f4)
  STEP=$(echo "${STATUS_RESP}" | grep -o '"step":"[^"]*' | cut -d'"' -f4)
  PROGRESS=$(echo "${STATUS_RESP}" | grep -o '"progress":[0-9]*' | cut -d':' -f2)

  echo "  ... Прогресс: ${PROGRESS}%, Шаг: ${STEP}, Статус: ${STATUS}"
  if [ "${STATUS}" == "succeeded" ] || [ "${STATUS}" == "failed" ]; then
    break
  fi
  sleep 2
done

if [ "${STATUS}" != "succeeded" ]; then
  echo "  ✗ Задание не успело завершиться или упало с ошибкой: ${STATUS_RESP}"
  exit 1
fi

echo "  ✓ Задание успешно завершено! Call ID = ${CALL_ID}"

# 5. Fetch Transcript
echo -e "\n[Шаг 5] Получение маскированного транскрипта звонка..."
TRANSCRIPT_RESP=$(curl -s -X GET "${API_URL}/v1/calls/${CALL_ID}/transcript?namespace=${NAMESPACE}&masked=true" \
  -H "Authorization: Bearer ${TOKEN}")
echo "  ✓ Текст транскрипта:"
echo "    $(echo "${TRANSCRIPT_RESP}" | grep -o '"full_text":"[^"]*' | cut -d'"' -f4)"

# 6. Fetch Analysis
echo -e "\n[Шаг 6] Получение структурированного разбора и метрик..."
ANALYSIS_RESP=$(curl -s -X GET "${API_URL}/v1/calls/${CALL_ID}/analysis?namespace=${NAMESPACE}" \
  -H "Authorization: Bearer ${TOKEN}")
echo "  ✓ Резюме диалога: $(echo "${ANALYSIS_RESP}" | grep -o '"summary":"[^"]*' | cut -d'"' -f4)"
echo "  ✓ Тип обращения: $(echo "${ANALYSIS_RESP}" | grep -o '"inquiry_type":"[^"]*' | cut -d'"' -f4)"

# 7. Test Text Clustering
echo -e "\n[Шаг 7] Проверка кластеризации пачки текстов (POST /v1/texts/analyze)..."
CLUSTERS_RESP=$(curl -s -X POST "${API_URL}/v1/texts/analyze" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "namespace": "'"${NAMESPACE}"'",
    "texts": [
      "щиты 3х6 в Балашихе",
      "билборд 3х6 шоссе Энтузиастов",
      "сколько стоит суперсайт в Москве",
      "прайс на суперсайты и скидки"
    ]
  }')
echo "  ✓ Сформированные кластеры:"
echo "    ${CLUSTERS_RESP}"

# 8. Clean up test data
echo -e "\n[Шаг 8] Каскадное удаление тестовых данных (DELETE /v1/data/delete)..."
DEL_RESP=$(curl -s -X DELETE "${API_URL}/v1/data/delete?namespace=${NAMESPACE}&call_id=${CALL_ID}" \
  -H "Authorization: Bearer ${TOKEN}")
echo "  ✓ Отчет об удалении: ${DEL_RESP}"

echo -e "\n================================================================="
echo "  СКВОЗНОЙ СЦЕНАРИЙ УСПЕШНО ПРОЙДЕН!"
echo "  Караоке-интерфейс доступен по адресу: ${API_URL}/dashboard"
echo "================================================================="
