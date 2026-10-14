#!/usr/bin/env bash
# E6: воспроизводимая проверка чистой машины.
#
# Что делает:
#   1. Проверяет, что рабочее дерево репозитория чистое (проверяем коммит, а не черновик).
#   2. Проверяет свободное место (нужно не менее 15 ГБ) и свободный порт,
#      падает с понятной причиной до клона/сборки.
#   3. Клонирует репозиторий в новую пустую папку на ДИСКЕ
#      ${CLEAN_CHECK_BASE:-${XDG_CACHE_HOME:-~/.cache}/apc-clean}/apc-clean-<дата>
#      (не в /tmp: там tmpfs в RAM, а клон + модели занимают ~3-7 ГБ).
#      В том же каталоге лежат и временные файлы прогона (логи, образцы).
#   4. В клоне выполняет ТОЛЬКО шаги из README «Быстрый старт»:
#      cp .env.example .env + APP_ENV=development, DEV_TOKEN, CALLBACK_SIGNING_SECRET.
#   5. Холодный старт: docker compose up -d --build, ожидание 8/8 healthy.
#      При падении печатает последние 40 строк лога и имя упавшего сервиса.
#   6. Выполняет docs/acceptance-requests.md по шагам, пишет сводку PASS/FAIL/WARN.
#   7. Пишет отчёт в reports/clean_machine_<дата>.md РЕПОЗИТОРИЯ-ИСТОЧНИКА
#      (без секретов и без текстов расшифровок — только коды, тайминги, счётчики).
#   8. Гасит стенд командой `docker compose -p <свой-project> down -v`
#      (только тома/сети этого прогона; чужой рабочий стек не трогает)
#      и удаляет временный клон и временные файлы прогона.
#
# Единственное отступление от README: хост-порт api переназначен на 8001
# (override-файл во временном каталоге прогона, клон не правится), потому что 8000
# на этой машине занят рабочим стеком. Сам контейнер слушает 8000, как по README.
# Порт переопределяется через CLEAN_CHECK_HOST_PORT. sudo не используется.
#
# Использование:
#   bash scripts/clean_machine_check.sh
#   CLEAN_CHECK_BASE=/data/clean bash scripts/clean_machine_check.sh
# Код выхода: 0 — все шаги PASS (WARN допустимы), 1 — есть FAIL либо стенд не встал.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAMP="$(date +%Y-%m-%d_%H%M)"
# База на диске, а не в /tmp (там tmpfs в RAM, а клон + скачанные модели
# занимают ~3-7 ГБ). Переопределение: CLEAN_CHECK_BASE=/data/clean.
BASE_DIR="${CLEAN_CHECK_BASE:-${XDG_CACHE_HOME:-$HOME/.cache}/apc-clean}"
CLONE_DIR="${BASE_DIR}/apc-clean-${STAMP}"
RUN_TMP="${BASE_DIR}/run-${STAMP}"
# Уникальный project name на прогон = свежие именованные тома (БД/S3) вместо
# переиспользования томов прошлого прогона: иначе контент-дедуп (C9) вернёт 200
# за чужое задание, а чистка прошлого прогона уже удалила его звонок (каскад
# ложных FAIL).
PROJECT="apc-clean-${STAMP//[_]/-}"
HOST_PORT="${CLEAN_CHECK_HOST_PORT:-8001}"
BASE="http://localhost:${HOST_PORT}"
NS="clean_msk"
REPORT="${REPO_ROOT}/reports/clean_machine_${STAMP}.md"
UPLOG="${RUN_TMP}/up.log"
RAWLOG="${RUN_TMP}/check.log"
MIN_DISK_GB=15
mkdir -p "${BASE_DIR}" "${RUN_TMP}"
: > "${RAWLOG}"
FAIL=0
WARN=0
UP_OK=1
COMPOSE=""
OVERRIDE_YML="$(mktemp "${RUN_TMP}/override-XXXXXX.yml")"
echo "лог сборки/старта: ${UPLOG}" | tee "${RAWLOG}"

pass() { echo "PASS: $1" | tee -a "${RAWLOG}"; }
fail() { echo "FAIL: $1" | tee -a "${RAWLOG}"; FAIL=$((FAIL + 1)); }
warn() { echo "WARN: $1" | tee -a "${RAWLOG}"; WARN=$((WARN + 1)); }
info() { echo "$1" | tee -a "${RAWLOG}"; }
sleep_between() { sleep 2; }

# Последние 40 строк лога сборки/старта + имя упавшего сервиса + его хвост.
# COMPOSE задаётся ниже (после .env); вызывается только после старта.
dump_failure_diagnostics() {
  info "--- последние 40 строк лога сборки/старта (${UPLOG}) ---"
  tail -n 40 "${UPLOG}" 2>/dev/null | tee -a "${RAWLOG}" || true
  info "--- состояние сервисов проекта ${PROJECT} ---"
  ${COMPOSE} ps 2>&1 | tee -a "${RAWLOG}" || true
  # Нездоровые/упавшие сервисы: всё, кроме healthy и одноразовых
  # alembic-migrate/model-downloader (им положено быть exited).
  local bad
  bad="$(${COMPOSE} ps --format json 2>/dev/null | python3 -c '
import json, sys
bad = []
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        s = json.loads(line)
    except ValueError:
        continue
    if not isinstance(s, dict):
        continue
    svc = s.get("Service", "?")
    if s.get("Health") == "healthy":
        continue
    if s.get("State") == "exited" and (
        s.get("ExitCode", 0) == 0
        or svc in ("alembic-migrate", "model-downloader")
    ):
        continue
    bad.append(svc)
print(" ".join(bad))
' 2>/dev/null || true)"
  if [ -n "${bad}" ]; then
    info "упавшие/нездоровые сервисы: ${bad}"
    local svc
    for svc in ${bad}; do
      info "--- последние 40 строк лога сервиса ${svc} ---"
      ${COMPOSE} logs --no-color --tail=40 "${svc}" 2>&1 | tee -a "${RAWLOG}" || true
    done
  else
    info "упавшие сервисы не опознаны (все healthy/exited-0 либо ps недоступен)"
  fi
}

# Снос только своего стенда: ${COMPOSE} всегда содержит -p ${PROJECT},
# поэтому down -v удаляет лишь тома/сети этого прогона; рабочий стек
# (другой project name) не затрагивается. rm -rf — только внутри BASE_DIR.
cleanup_run() {
  if [ -n "${COMPOSE}" ] && [ -d "${CLONE_DIR}" ]; then
    (cd "${CLONE_DIR}" && ${COMPOSE} down -v >>"${RAWLOG}" 2>&1) || true
  fi
  rm -f "${OVERRIDE_YML}" 2>/dev/null || true
  case "${CLONE_DIR}" in
    "${BASE_DIR}/"*) rm -rf "${CLONE_DIR}" || true ;;
  esac
  case "${RUN_TMP}" in
    "${BASE_DIR}/"*) rm -rf "${RUN_TMP}" || true ;;
  esac
}

# code_of <outfile> <curl-args...>: curl c записью тела в файл и печатью кода;
# при 429 ждёт окно лимита (11 с) и повторяет один раз.
code_of() {
  local out="$1"; shift
  local code
  code="$(curl -s -o "${out}" -w '%{http_code}' "$@")"
  if [ "${code}" = "429" ]; then
    sleep 11
    code="$(curl -s -o "${out}" -w '%{http_code}' "$@")"
  fi
  echo "${code}"
}

# --- 0. Чистота дерева, место на диске, свободный порт, нужные команды --------
# Порядок: сначала дешёвые проверки с понятной причиной падения, чтобы не
# упираться в долгое «compose up неуспешен дважды» из-за занятого порта
# или переполненного диска.
cd "${REPO_ROOT}"
# Проверяем только трекаемые файлы: нетронутый скрипт в рабочей копии на клон
# не влияет (клон содержит только закоммиченный HEAD, чей SHA пишется в отчёт).
if [ -n "$(git status --porcelain | grep -v '^??')" ]; then
  echo "FAIL: рабочее дерево нечистое — проверяем только закоммиченное состояние"
  exit 1
fi
HEAD_SHA="$(git rev-parse --short HEAD)"

# Место: клон + модели (~3-7 ГБ) + сборка; нужно не менее MIN_DISK_GB.
AVAIL_KB="$(df -k --output=avail "${BASE_DIR}" 2>/dev/null | tail -n 1 | tr -d ' ' || true)"
if ! [[ "${AVAIL_KB}" =~ ^[0-9]+$ ]]; then
  echo "FAIL: не могу проверить свободное место на ${BASE_DIR} (df неуспешен)"
  exit 1
fi
if [ "${AVAIL_KB}" -lt "$((MIN_DISK_GB * 1024 * 1024))" ]; then
  echo "FAIL: на ${BASE_DIR} свободно ~$((AVAIL_KB / 1024 / 1024)) ГБ, нужно не менее ${MIN_DISK_GB} ГБ (клон + модели + сборка). Освободите место или задайте CLEAN_CHECK_BASE=<каталог-на-диске-с-местом>"
  exit 1
fi

# Порт: занят — либо рабочим стеком, либо чужим процессом. /dev/tcp ловит
# и не-HTTP занятие (curl один его бы пропустил).
port_busy() { (echo > "/dev/tcp/127.0.0.1/$1") >/dev/null 2>&1; }
if port_busy "${HOST_PORT}" || (curl -s -m 2 -o /dev/null "http://localhost:${HOST_PORT}/health"); then
  echo "FAIL: порт ${HOST_PORT} уже занят (возможно, рабочим стеком). Освободите порт или задайте CLEAN_CHECK_HOST_PORT=<свободный-порт>"
  exit 1
fi

for _cmd in git docker curl python3 ffmpeg; do
  if ! command -v "${_cmd}" >/dev/null 2>&1; then
    echo "FAIL: нет команды ${_cmd} в PATH — установите и повторите"
    exit 1
  fi
done

# --- 1. Клон в новую пустую папку -------------------------------------------
mkdir -p "${CLONE_DIR}"
if [ -n "$(ls -A "${CLONE_DIR}")" ]; then
  echo "FAIL: ${CLONE_DIR} не пуста" | tee "${RAWLOG}"
  exit 1
fi
git clone -q "${REPO_ROOT}" "${CLONE_DIR}" >>"${RAWLOG}" 2>&1 \
  || { echo "FAIL: git clone" | tee -a "${RAWLOG}"; cleanup_run; exit 1; }
cd "${CLONE_DIR}"
info "клон: ${REPO_ROOT}@${HEAD_SHA} -> ${CLONE_DIR}"

# --- 2. Только README + .env.example -----------------------------------------
cp .env.example .env
TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
sed -i 's/^APP_ENV=.*/APP_ENV=development/' .env
sed -i 's/^DEV_TOKEN=.*/DEV_TOKEN=__SET_BY_CHECK_SCRIPT__/' .env
sed -i 's/^CALLBACK_SIGNING_SECRET=.*/CALLBACK_SIGNING_SECRET=__SET_BY_CHECK_SCRIPT__/' .env
# Подстановка секретов без засветки в лог: python, не sed с секретом в argv-ле?
# (argv виден в ps; файл .env живёт только во временном клоне, в отчёт секреты не идут).
python3 - "${TOKEN}" "${SECRET}" <<'EOF'
import sys
p = ".env"
s = open(p).read()
s = s.replace("__SET_BY_CHECK_SCRIPT__", sys.argv[1], 1)
s = s.replace("__SET_BY_CHECK_SCRIPT__", sys.argv[2], 1)
open(p, "w").write(s)
EOF
grep -q '^APP_ENV=development$' .env || { echo "FAIL: .env APP_ENV" | tee -a "${RAWLOG}"; cleanup_run; exit 1; }

# Override только для хост-порта (см. шапку скрипта). !override — заменить
# маппинг целиком: без него compose добавляет 8001:8000 к штатному 8000:8000,
# и создание api падает на конфликте с рабочим стеком на хосте.
cat > "${OVERRIDE_YML}" <<EOF
services:
  api:
    ports: !override ["${HOST_PORT}:8000"]
EOF

# --- 3. Холодный старт --------------------------------------------------------
info "старт: docker compose -p ${PROJECT} up -d --build"
COMPOSE="docker compose -p ${PROJECT} -f docker-compose.yml -f ${OVERRIDE_YML}"
if ! ${COMPOSE} up -d --build >>"${UPLOG}" 2>&1; then
  info "первый up неуспешен, пауза 20 с и повтор (холодная сборка/скачивание)"
  sleep 20
  ${COMPOSE} up -d --build >>"${UPLOG}" 2>&1 || UP_OK=0
fi

if [ "${UP_OK}" = "1" ]; then
healthy=0
# NOTE: `ps --format json` печатает по JSON-объекту на строку (не массив) —
# парсим построчно, иначе счётчик вечно 0.
for i in $(seq 1 120); do
  healthy="$(${COMPOSE} ps --format json 2>/dev/null | python3 -c '
import json, sys
n = 0
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        s = json.loads(line)
    except ValueError:
        continue
    if not isinstance(s, dict):
        continue
    if s.get("Health") == "healthy":
        n += 1
    elif s.get("State") == "exited" and (
        s.get("ExitCode", 0) == 0
        or s.get("Service") in ("alembic-migrate", "model-downloader")
    ):
        n += 1
print(n)
' 2>/dev/null || echo 0)"
  if [ "${healthy}" = "8" ]; then break; fi
  if [ "$((i % 6))" = "0" ]; then info "ожидание: ${healthy}/8 (попытка ${i}/120)"; fi
  sleep 10
done
info "healthy/exited сервисов: ${healthy}/8"
if [ "${healthy}" != "8" ]; then
  dump_failure_diagnostics
  fail "холодный старт: ${healthy}/8 (лимит 40 мин)"
else
  pass "холодный старт: 8/8 healthy"
fi

auth() { echo "Authorization: Bearer ${TOKEN}"; }
jget() { python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get(sys.argv[1], ""))' "$1" 2>/dev/null; }

# --- Приёмка ------------------------------------------------------------------
# Шаг 0. Живость и готовность (503 на /ready в прогрев допустим — ждём до 5 мин).
if [ "$(curl -s -o /dev/null -w '%{http_code}' "${BASE}/health")" = "200" ] \
  && curl -s "${BASE}/health" | grep -q '"status"'; then
  pass "0a GET /health 200"
else fail "0a GET /health"; fi
sleep_between
ready_ok=0
for _ in $(seq 1 30); do
  if [ "$(curl -s -o /dev/null -w '%{http_code}' -H "$(auth)" "${BASE}/ready")" = "200" ]; then
    ready_ok=1; break
  fi
  sleep 10
done
if [ "${ready_ok}" = "1" ]; then pass "0b GET /ready 200"; else fail "0b GET /ready"; fi
sleep_between
if [ "$(curl -s -o /dev/null -w '%{http_code}' "${BASE}/metrics")" = "200" ]; then
  pass "0c GET /metrics 200"
else fail "0c GET /metrics"; fi
sleep_between

# Шаг 1. Приём аудио.
curl -s -H "$(auth)" "${BASE}/v1/voice/sample-audio" -o "${RUN_TMP}/clean_sample.wav"
if python3 -c "import sys; assert open('${RUN_TMP}/clean_sample.wav','rb').read(4)==b'RIFF'" 2>/dev/null; then
  pass "1 sample-audio: валидный wav"
else fail "1 sample-audio"; fi
sleep_between
UP_RAW="$(curl -s -w '\n%{http_code}' -X POST "${BASE}/v1/jobs/upload" -H "$(auth)" \
  -F "namespace=${NS}" -F "file=@${RUN_TMP}/clean_sample.wav" \
  -F "external_id=clean-001" -F "direction=inbound" -F "manager_id=mgr-1" -F "client_id=cli-1")"
CODE1="$(echo "${UP_RAW}" | tail -1)"
UP1="$(echo "${UP_RAW}" | head -n -1)"
JOB="$(echo "${UP1}" | jget job_id)"
if [ "${CODE1}" = "202" ] && [ -n "${JOB}" ] && echo "${UP1}" | grep -q status_url; then
  pass "1a upload 202 + job_id + status_url"
else fail "1a upload (код ${CODE1})"; fi
sleep_between
UPA="$(curl -s -X POST "${BASE}/v1/jobs/upload" -H "$(auth)" -H "Idempotency-Key: clean-001" \
  -F "namespace=${NS}" -F "file=@${RUN_TMP}/clean_sample.wav")"
UPB="$(curl -s -X POST "${BASE}/v1/jobs/upload" -H "$(auth)" -H "Idempotency-Key: clean-001" \
  -F "namespace=${NS}" -F "file=@${RUN_TMP}/clean_sample.wav")"
if [ "$(echo "${UPA}" | jget job_id)" = "$(echo "${UPB}" | jget job_id)" ] \
  && [ -n "$(echo "${UPA}" | jget job_id)" ]; then
  pass "1b идемпотентность: повтор тем же ключом — тот же job_id"
else fail "1b идемпотентность"; fi
sleep_between
# SSRF-защита: приватный адрес обязан отклоняться 4xx-конвертом.
SSRF_CODE="$(code_of "${RUN_TMP}/clean_ssrf.json" -X POST "${BASE}/v1/jobs/by-url" \
  -H "$(auth)" -H "Content-Type: application/json" \
  -d "{\"namespace\":\"${NS}\",\"url\":\"http://127.0.0.1:9/call.wav\"}")"
if echo "${SSRF_CODE}" | grep -qE '^(400|422)$' \
  && python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert set(d)=={"error","message","details"}' "${RUN_TMP}/clean_ssrf.json"; then
  pass "1в SSRF-блок: приватный URL -> ${SSRF_CODE} + конверт"
else fail "1в SSRF-блок (код ${SSRF_CODE})"; fi
sleep_between
BYURL_CODE="$(code_of "${RUN_TMP}/clean_byurl.json" -X POST "${BASE}/v1/jobs/by-url" \
  -H "$(auth)" -H "Content-Type: application/json" \
  -d "{\"namespace\":\"${NS}\",\"url\":\"https://example.com/call.wav\"}")"
if echo "${BYURL_CODE}" | grep -qE '^(202|400|422)$' \
  && python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "${RUN_TMP}/clean_byurl.json"; then
  pass "1в by-url публичный: код ${BYURL_CODE}, валидный JSON"
else fail "1в by-url публичный (код ${BYURL_CODE})"; fi
sleep_between
S3_CODE="$(code_of "${RUN_TMP}/clean_bys3.json" -X POST "${BASE}/v1/jobs/by-s3" \
  -H "$(auth)" -H "Content-Type: application/json" \
  -d "{\"namespace\":\"${NS}\",\"s3_key\":\"no-such-key.wav\",\"s3_bucket\":\"raw-audio\"}")"
if echo "${S3_CODE}" | grep -qE '^(400|404|422)$' \
  && python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert set(d)=={"error","message","details"}' "${RUN_TMP}/clean_bys3.json"; then
  pass "1г by-s3 отсутствующий ключ -> ${S3_CODE} + конверт"
else fail "1г by-s3 (код ${S3_CODE})"; fi
sleep_between

# Шаг 2. Ожидание задания (до 600 с).
STATUS=""; CALL=""
for _ in $(seq 1 120); do
  ST="$(curl -s "${BASE}/v1/jobs/${JOB}?namespace=${NS}" -H "$(auth)")"
  STATUS="$(echo "${ST}" | jget status)"
  CALL="$(echo "${ST}" | jget call_id)"
  [ "${STATUS}" = "succeeded" ] && break
  [ "${STATUS}" = "failed" ] && break
  sleep 5
done
if [ "${STATUS}" = "succeeded" ] && [ -n "${CALL}" ]; then
  pass "2 job succeeded + call_id"
else fail "2 job (статус ${STATUS})"; fi
sleep_between

# Шаг 3. Говорящие (без текстов — только структура).
SEG="$(curl -s "${BASE}/v1/calls/${CALL}/transcript?namespace=${NS}&format=json" -H "$(auth)")"
if echo "${SEG}" | python3 -c '
import json, sys
d = json.load(sys.stdin)
segs = d.get("segments", [])
assert segs, "нет сегментов"
s0 = segs[0]
assert {"speaker_label", "speaker_role", "start", "end"} <= set(s0), "нет полей спикера"
assert isinstance(s0["start"], (int, float)) and isinstance(s0["end"], (int, float))
print("segments:", len(segs))
' >>"${RAWLOG}" 2>&1; then
  pass "3 транскрипт: сегменты + speaker_label/role + таймкоды"
else fail "3 транскрипт"; fi
sleep_between

# Шаг 4. Голосовой поиск (фиксируем латентность; 503-timeout — документированное поведение).
V_START="$(date +%s%N)"
V_RESP="$(curl -s -w '\n%{http_code}' -X POST "${BASE}/v1/voice/search?namespace=${NS}&language=ru" \
  -H "$(auth)" -F "file=@${RUN_TMP}/clean_sample.wav;type=audio/wav")"
V_CODE="$(echo "${V_RESP}" | tail -1)"
V_BODY="$(echo "${V_RESP}" | head -n -1)"
V_MS="$(( ($(date +%s%N) - V_START) / 1000000 ))"
if [ "${V_CODE}" = "200" ] && echo "${V_BODY}" | python3 -c 'import json,sys; assert json.load(sys.stdin).get("text")'; then
  pass "4 voice/search 200 за ${V_MS} мс (текст не пуст)"
elif [ "${V_CODE}" = "503" ] && echo "${V_BODY}" | grep -q voice_search_timeout; then
  warn "4 voice/search 503 voice_search_timeout за ${V_MS} мс (допущено документацией)"
else fail "4 voice/search (код ${V_CODE}, ${V_MS} мс)"; fi
sleep_between

# Шаг 5. Разбор по схеме (только наличие полей и версий, без содержимого).
AN="$(curl -s "${BASE}/v1/calls/${CALL}/analysis?namespace=${NS}" -H "$(auth)")"
if echo "${AN}" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert d.get("schema_version") and d.get("prompt_version"), "нет версий"
assert isinstance(d.get("inquiry_type"), str), "нет inquiry_type"
for k in ("entities", "manager_checklist", "deterministic_speech_metrics"):
    assert k in d, f"нет {k}"
print("schema:", d["schema_version"], "prompt:", d["prompt_version"], "type:", d["inquiry_type"])
' >>"${RAWLOG}" 2>&1; then
  pass "5 analysis: версии схемы/промпта + тип + сущности + чек-лист + метрики"
else fail "5 analysis"; fi
sleep_between

# Шаг 6. Сводка, тексты, CSV.
SUM_CODE="$(curl -s -o /dev/null -w '%{http_code}' \
  "${BASE}/v1/analytics/summary?namespace=${NS}&date_from=2026-09-01T00:00:00Z&date_to=2026-10-13T00:00:00Z" -H "$(auth)")"
if [ "${SUM_CODE}" = "200" ]; then pass "6a analytics/summary 200"; else fail "6a summary (${SUM_CODE})"; fi
sleep_between
# Пейлоад через файл (кириллица в argv бьётся на некоторых шеллах; тексты
# синтетические, не из расшифровок).
cat > "${RUN_TMP}/clean_texts.json" <<'EOF'
{"namespace": "demo_outdoor_msk",
 "texts": ["щиты 3х6 в Балашихе на октябрь", "узнать прайс на суперсайт"]}
EOF
CLU="$(curl -s -X POST "${BASE}/v1/texts/analyze" -H "$(auth)" -H "Content-Type: application/json" \
  --data-binary "@${RUN_TMP}/clean_texts.json")"
if echo "${CLU}" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("clusters") is not None' 2>/dev/null; then
  pass "6b texts/analyze 200 + кластеры"
else fail "6b texts/analyze"; fi
sleep_between
CSV_CODE="$(code_of "${RUN_TMP}/clean_export.csv" \
  "${BASE}/v1/analytics/export_csv?namespace=${NS}&date_from=2026-09-01T00:00:00Z" -H "$(auth)")"
if [ "${CSV_CODE}" = "200" ] && [ -s "${RUN_TMP}/clean_export.csv" ]; then
  pass "6c export_csv 200 + непустой CSV"
else fail "6c export_csv (${CSV_CODE})"; fi
sleep_between

# Шаг 8 (до удалений). Форматы, список, поиск.
FMT_OK=1
for F in json text srt vtt; do
  C="$(code_of /dev/null \
    "${BASE}/v1/calls/${CALL}/transcript?namespace=${NS}&format=${F}" -H "$(auth)")"
  [ "${C}" = "200" ] || FMT_OK=0
  sleep_between
done
if [ "${FMT_OK}" = "1" ]; then pass "8a транскрипт: json/text/srt/vtt по 200"; else fail "8a форматы"; fi
LIST_CODE="$(code_of /dev/null "${BASE}/v1/calls?namespace=${NS}&limit=5" -H "$(auth)")"
if [ "${LIST_CODE}" = "200" ]; then pass "8b GET /v1/calls 200"; else fail "8b список (${LIST_CODE})"; fi
sleep_between
Q_CODE="$(code_of /dev/null \
  "${BASE}/v1/calls?namespace=${NS}&q=%D1%89%D0%B8%D1%82&limit=5" -H "$(auth)")"
if [ "${Q_CODE}" = "200" ]; then pass "8c поиск q=%D1%89... 200"; else fail "8c поиск (${Q_CODE})"; fi
sleep_between

# Шаг 7. Удаления одной ручкой.
D1="$(code_of /dev/null -X DELETE \
  "${BASE}/v1/data/delete?namespace=${NS}&call_id=${CALL}" -H "$(auth)")"
[ "${D1}" = "200" ] && pass "7a delete по call_id 200" || fail "7a delete call (${D1})"
sleep_between
D2="$(code_of /dev/null -X DELETE \
  "${BASE}/v1/data/delete?namespace=${NS}&participant_id=cli-1" -H "$(auth)")"
[ "${D2}" = "200" ] && pass "7b delete по participant_id 200" || fail "7b delete participant (${D2})"
sleep_between
D3="$(code_of /dev/null -X DELETE \
  "${BASE}/v1/data/delete?namespace=${NS}&date_from=2026-01-01T00:00:00Z&date_to=2026-12-31T00:00:00Z" -H "$(auth)")"
[ "${D3}" = "200" ] && pass "7c delete по периоду 200" || fail "7c delete период (${D3})"
sleep_between
HIST_CODE="$(code_of /dev/null "${BASE}/v1/voice/history?namespace=${NS}" -H "$(auth)")"
if [ "${HIST_CODE}" = "200" ]; then pass "7d voice/history 200"; else fail "7d history (${HIST_CODE})"; fi
sleep_between

# Шаг 9. Задание с callback (только создание — приёмник вне контура).
# Отдельный синтетический файл: повтор sample.wav дал бы 200 по контент-дедупу (C9),
# а здесь проверяется именно создание задания — нужен другой хэш.
ffmpeg -y -v error -f lavfi -i "sine=frequency=440:duration=3" \
  -ar 16000 -ac 1 -c:a pcm_s16le "${RUN_TMP}/clean_callback.wav" \
  || { fail "9 синтез wav для callback"; CB_CODE="000"; }
if [ -f "${RUN_TMP}/clean_callback.wav" ]; then
CB_CODE="$(code_of /dev/null -X POST "${BASE}/v1/jobs/upload" -H "$(auth)" \
  -F "namespace=${NS}" -F "file=@${RUN_TMP}/clean_callback.wav" \
  -F "callback_url=https://example.com/hook")"
if [ "${CB_CODE}" = "202" ]; then pass "9 upload с callback_url 202"; else fail "9 callback job (${CB_CODE})"; fi
fi
sleep_between

# Шаг 10. Дашборд и Битрикс.
DASH="$(code_of /dev/null "${BASE}/dashboard")"
[ "${DASH}" = "200" ] && pass "10a dashboard 200" || fail "10a dashboard (${DASH})"
sleep_between
BX="$(curl -s -X POST "${BASE}/v1/integrations/bitrix24/webhook?namespace=${NS}&token=${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"event":"ONVOXIMPLANTCALLFINISHED","data":{"CALL_ID":"crm-1"}}')"
if echo "${BX}" | grep -q missing_record_url; then
  pass "10b bitrix без RECORD_URL: missing_record_url"
else fail "10b bitrix"; fi

# Финальная чистка тестовых данных (не оценивается).
curl -s -o /dev/null -X DELETE \
  "${BASE}/v1/data/delete?namespace=${NS}&date_from=2026-01-01T00:00:00Z&date_to=2026-12-31T00:00:00Z" -H "$(auth)" \
  >>"${RAWLOG}" 2>&1
info "чистка периодом выполнена (best-effort)"

else
  dump_failure_diagnostics
  fail "холодный старт: compose up неуспешен дважды (диагностика выше: хвост лога + упавшие сервисы)"
fi

# --- 4. Отчёт (без секретов и текстов) -----------------------------------------
{
  echo "# Проверка чистой машины E6 (${STAMP})"
  echo ""
  echo "- Источник: коммит ${HEAD_SHA}; клон: ${CLONE_DIR}."
  echo "- Стек: \`${PROJECT}\` (уникальный project name = свежие тома БД/S3 на прогон)."
  echo "- Настройка клона — только README + .env.example:"
  echo "  \`cp .env.example .env\`, \`APP_ENV=development\`, случайные \`DEV_TOKEN\`"
  echo "  и \`CALLBACK_SIGNING_SECRET\`, \`APP_PROFILE=small\` (дефолт примера)."
  echo "- Единственное отступление: хост-порт api \`${HOST_PORT}\` вместо 8000"
  echo "  (8000 занят рабочим стеком; контейнер слушает 8000; клон не правлен)."
  echo "- В отчёте нет секретов и текстов расшифровок: только коды, тайминги, счётчики."
  echo ""
  echo "## Сводка по шагам приёмки"
  echo ""
  grep -E '^(PASS|FAIL|WARN): ' "${RAWLOG}" | sed 's/^/  - /'
  echo ""
  echo "## Детали (счётчики, версии, латентность)"
  echo ""
  grep -vE '^(PASS|FAIL|WARN): ' "${RAWLOG}" | sed 's/^/  - /'
  echo ""
  if [ "${FAIL}" = "0" ]; then
    echo "**Итог: приёмка пройдена целиком (FAIL=0, WARN=${WARN}).**"
  else
    echo "**Итог: приёмка НЕ пройдена целиком (FAIL=${FAIL}, WARN=${WARN}).**"
  fi
} > "${REPORT}"
# Страховка: секреты в отчёт попасть не должны (токен/сикрет — только во временном клоне).
if grep -qE "${TOKEN}|${SECRET}" "${REPORT}"; then
  echo "FAIL: секрет попал в отчёт" | tee -a "${RAWLOG}"
  FAIL=$((FAIL + 1))
fi

# --- 5. Гашение стенда и удаление временных данных -------------------------------
# down -v ограничен своим project name (см. cleanup_run): удаляются только
# тома/сети этого прогона, чужой рабочий стек не затрагивается.
# Затем удаляются временный клон и временные файлы прогона (RUN_TMP с логами —
# после того, как отчёт уже собран; в отчёт секреты и тексты не входят).
cleanup_run

echo "отчёт: ${REPORT}; FAIL=${FAIL} WARN=${WARN}; клон и тома прогона удалены"
[ "${FAIL}" = "0" ]
