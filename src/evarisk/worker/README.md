# worker

Назначение: отдельный от API процесс, который по частоте каждого адаптера обновляет сырое хранилище, ведёт состояние источников в `db.source_state` и выполняет задания из `db.jobs` (Т6). Запуск `make worker` или `python -m evarisk.worker`.

## Интерфейс
- `loop.run_once(now, store, fetcher, registry, repo) -> dict[str, str]`: один проход по адаптерам реестра; в начале `repo.mark_attempt(WORKER_SOURCE, now)` (строка `_worker`, из неё `GET /health` берёт `worker_last_tick`). Источник в состоянии `frozen` или `disabled` даёт `skipped: <состояние>`; при `now - last_success_at < cadence` даёт `skipped: not due`; иначе `mark_attempt`, `fetch_current`, `mark_success` и `fetched N`. `FetchError`, `ValueError`, `OSError` пишутся в `last_error` через `mark_error`, печатаются и дают `error: <тип>: <текст>`; остальные исключения поднимаются наверх. Ошибка одного адаптера не прерывает остальные.
- `loop.run_forever(settings, *, registry=None, sleep=time.sleep, run_jobs=None)`: создаёт движок по `settings.database_url`, схему (`init_schema`), `RawStore(settings.raw_root)`, `HttpFetcher`; в цикле `run_once(utc_now(), ...)`, печать сводки, затем `run_jobs`; по умолчанию это `jobs.run_pending` с тем же реестром, репозиторием состояний и fetcher, печать `jobs handled=N` при N > 0; пауза `settings.worker_tick_s` шагами по 1 с. Останавливается по SIGTERM после текущего прохода; прежний обработчик сигнала восстанавливается.
- `jobs.run_pending(repo, store, settings, now, *, registry=None, states=None, fetcher=None, clock=utc_now) -> int`: сначала `repo.requeue_stale(now, STALE_AFTER)` (10 мин), затем `claim(now)` по одному до пустой очереди. `kind == "assessment"`: `AssessmentRequest.model_validate(params)` и `run_assessment(req, store, settings, on_progress)`, где `on_progress` пишет `repo.progress(job.id, step, fraction, clock())`; `finish` с id результата. `kind == "refresh"`: адаптер по `params["source"]` из реестра, состояние источника должно быть `active` (иначе отказ с текстом), `mark_attempt`, `fetch_current`, `mark_success` или `mark_error` для `FetchError`, `ValueError`, `OSError`; `progress("fetched N", 1.0)` и `finish(None)`. Любое исключение задания записывается `fail(job.id, "<Тип>: <текст>")`, traceback печатается в stderr, следующее задание выполняется. Возвращает число взятых заданий. `now` штампует `requeue_stale` и `claim`, `clock()` остальные шаги.
- `__main__`: `run_forever(Settings())`.

## Инварианты
- `now` tz-aware UTC; наивная дата это `ValueError`.
- Отказ источника записывается и виден в `source_state`, но никогда не превращается в пустое «благополучие»: воркер только сохраняет байты, оценку делают линии через `AsOf`.
- CelesTrak опрашивается только отсюда и только по cadence (D33, usage policy).

## Как тестировать
- `uv run pytest tests/worker -q`: фальшивые адаптеры, SQLite в памяти, `respx` для HTTP; живой запуск в тестах запрещён. Задания с реальной фикстурой хранилища проверяются в `tests/e2e/test_jobs_api.py` вызовом `run_pending` вручную.

## Докачка архива в задании расчёта (D59)
- `jobs._run_assessment` сообщает шаг `archive`, вызывает `store.backfill.backfill` для режимов `review` и `replay` при `settings.archive_backfill`, затем считает; строки отчёта идут в `run_assessment(extra_limitations=...)`, а при отказе «нет эфемериды» в текст ошибки задания.
- `run_pending(..., archive_fetch=...)` подменяет загрузку в тестах: `tests/e2e/test_worker_backfill.py`. Фикстура `tests/e2e/conftest.py` держит `archive_backfill=False`.
- `loop._pause(..., between)` опрашивает очередь заданий после каждого шага паузы (1 с): задание со страницы стартует сразу, источники по-прежнему опрашиваются раз в тик.

## Чего не делать
- Не глотать исключения вне списка `RECORDED_ERRORS` в проходе по адаптерам. В заданиях исключение не глотается, а становится текстом `error` задания и печатается.
- Не считать риск и не читать таблицы вместо `AsOf`.
