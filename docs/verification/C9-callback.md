# C9-callback. Сквозная доставка callback — живая проверка (2026-10-11)

Инфра: `docker-compose.test.yml` (приёмник `callback-receiver` в сети `backend`,
без egress) + флаг `CALLBACK_ALLOW_PRIVATE_NET=true` через /tmp-оверрайд
(в репозитории только выключенный дефолт). Приёмник: первые `FAIL_FIRST_N=1`
запросов — 500, дальше 200; независимо пересчитывает HMAC и свежесть `ts`.

## Ход

Звонок `c9_cb_live3` (`6e921b84-…`, 11,2 с) с
`callback_url=http://callback-receiver:8081/hook` → `succeeded` → доставка:

| попытка | приёмник | воркер |
|---|---|---|
| 1 | 500 (запрограммирован), `signature_ok=true`, `ts_fresh=true` | `callback_failed_status`, пауза 5 с |
| 2 (+5 с) | 200, `signature_ok=true`, `ts_fresh=true`, `job_id`/`status=succeeded` верные | `callback_delivered_successfully` |

Подпись `X-Apc-Signature` пересчитана приёмником независимо (HMAC
`"{ts}.{raw_body}"`, секрет общий) — обе попытки `true`; `X-Apc-Timestamp`
свежий (≤5 мин) — обе `true`. Ретрай при 500 доказан живьём (полное расписание
[5,15,60,300,900] — юнитом `test_dispatch_callback_retries_five_times_then_gives_up`).

## Охрана флага

- `CALLBACK_ALLOW_PRIVATE_NET=false` по умолчанию (`.env.example`); scope —
  только callback-URL (скачивание по ссылке всегда строгое:
  `download_file_ssrf_safe`, без исключений).
- Production + флаг → отказ старта api (lifespan) и worker-callback (main):
  `enforce_production_callback_policy`, тест
  `test_production_refuses_private_callback_flag`.
- После проверки тестовые оверрайды сняты, api/worker-callback пересозданы
  на штате, приёмник остановлен.

## Расхождения с ТЗ

Нет.
