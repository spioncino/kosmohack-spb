# db

Назначение: таблицы состояния источников и заданий на SQLAlchemy Core, без ORM и миграций (D38). Таблицы производные: сырое хранилище `store/` остаётся источником истины, результаты расчётов остаются JSON-файлами.

## Интерфейс
- `SourceStateRepo.disabled_sources() -> frozenset[str]`: источники в состоянии `disabled`; API и воркер передают их в `run_assessment(..., disabled=...)` (D51).
- `schema.metadata`: таблицы `source_state(source PK, state, changed_at, reason, last_attempt_at, last_success_at, last_error)` и `jobs(id PK, kind, params JSON, status, progress JSON, error, created_at, started_at, finished_at, assessment_id)`.
- `jobs.JobsRepo(engine)`: `create(kind, params, now) -> Job` (`kind` из `assessment`, `refresh`, иначе `ValueError`; статус `queued`, id 12 hex), `get(job_id) -> Job | None`, `claim(now) -> Job | None` (самое старое `queued` по `created_at, id` переводится в `running` со `started_at` и `progress = {step: claimed, fraction: 0}`; в SQLite один `UPDATE … WHERE status='queued' AND id=(SELECT … LIMIT 1) RETURNING id`, в Postgres `SELECT … FOR UPDATE SKIP LOCKED` и `UPDATE` в одной транзакции; ветка по `engine.dialect.name`), `progress(job_id, step, fraction, now)` (`progress = {step, fraction, at}`, доля вне `[0, 1]` это `ValueError`), `finish(job_id, assessment_id, now)` (`done`, `finished_at`, `error = None`), `fail(job_id, error, now)` (`failed`, `finished_at`, текст ошибки), `requeue_stale(now, older_than) -> int` (задания `running` со `started_at` старше порога возвращаются в `queued` с `started_at = None` и `progress = {step: requeued, fraction: 0, at, note}`; сравнение времён в Python, не в SQL). `progress`, `finish`, `fail` для неизвестного id это `KeyError`. Статусы `queued`, `running`, `done`, `failed`.
- `source_state.WORKER_SOURCE = "_worker"`: строка `source_state`, которую воркер отмечает `mark_attempt` на каждом тике; API читает из неё `worker_last_tick`.
- `schema.UtcDateTime`: `TypeDecorator`, в Postgres `TIMESTAMP(timezone=True)`, в SQLite ISO-строка с зоной; на выходе всегда tz-aware UTC; наивная дата на входе это `ValueError`.
- `engine.make_engine(url) -> Engine`: для `sqlite:///:memory:` один общий коннект (`StaticPool`), для файла SQLite создаётся каталог, ставится `connect_args={"timeout": 30}` и на каждое подключение (событие `connect`) выполняются `PRAGMA journal_mode=WAL` и `PRAGMA busy_timeout=30000`: в один файл пишут api и воркер; для Postgres настроек не добавляется. `engine.init_schema(engine)` создаёт таблицы.
- `source_state.SourceStateRepo(engine)`: `get(source) -> SourceState` (неизвестный источник это `active` без записи), `all()`, `set_state(source, state, reason, now)`, `mark_attempt(source, now)`, `mark_success(source, now)` (ставит `last_success_at` и `last_attempt_at`, стирает `last_error`), `mark_error(source, error, now)` (ставит `last_error` и `last_attempt_at`). Состояния `active`, `frozen`, `disabled`; другое значение это `ValueError`.
- Запись через `_merge`: сначала `update`, при `rowcount == 0` `insert`; диалект-независимо.

## Инварианты
- Все времена tz-aware UTC на входе и на выходе; `reason` и `last_error` это `None`, пока их не задали, а не пустая строка.
- Нет миграций: схема пересоздаётся `init_schema`, данные восстанавливаются из сырого хранилища и повторных запусков.

## Как тестировать
- `uv run pytest tests/db -q`; SQLite в памяти, без Postgres и сети.

## Чего не делать
- Не читать таблицы из `risk/`, `windows/`, `orbit/`, `replay/`: данные только через `AsOf`.
- Не хранить в таблицах результаты расчётов и сырые ответы источников.
