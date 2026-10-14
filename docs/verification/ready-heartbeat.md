# /ready heartbeat — живая проверка (2026-10-12)

Код: `app/workers/warmup.py::start_heartbeat_thread` (daemon-поток со своим
циклом и собственным Redis-клиентом, тик 30 с, TTL флага 90 с),
`worker-long`/`worker-fast` стартуют его в `main()`; тест
`tests/test_warmup.py::test_heartbeat_loop_refreshes_during_long_job` (+ daemon-тест).

## Почему не хватило asyncio-задачи

Первая версия слала heartbeat задачей в том же event loop. Проверка показала:
`process_job` вызывает `asr_engine.transcribe` синхронно — цикл блокируется
на минуты, задача голодает, флаг протухает. Отдельный поток (как требует пункт)
с GIL, отпущенным CTranslate2 на время инференса, тикает дальше.

## Живой прогон

20-минутная синтетика (`long20.mp3`, 1232 с) на worker-long, опрос
`GET /ready` каждые 5 с весь период обработки: **240/240 → 200, 0 просадок**;
TTL флага в Redis свежий (84–87 с) прямо во время чанков инференса.

## Остановка

`docker stop worker-fast` → `/ready` = 503 через 86 с (истечение TTL 90 с,
heartbeat мёртв вместе с процессом). `docker start` → 200 после прогрева.
Поведение «упал — видно» сохранено.
