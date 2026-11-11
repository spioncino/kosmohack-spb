# devtools

Назначение: автоматические проверки правил проекта для `make check` и хука pre-commit. Модуль не участвует в
расчёте и не импортируется сервисом.

## Интерфейс
- `honesty.find_violations(root) -> list[Violation]`: нарушения правил честности в `src/evarisk`: `datetime.now()`
  без зоны и `utcnow()`, обращения к читателю, TODO и FIXME в комментариях; в модулях `risk`, `windows`, `orbit`,
  `replay` также подстановка нуля вместо отсутствия данных, чтение таблиц в обход `AsOf` и молчаливый `except`.
  Запуск: `scripts/check_honesty.py`.
- `docs_check.module_of(path) -> str | None`, `missing_module_readme(src_root) -> list[Path]`,
  `modules_without_status(changed, status_text) -> list[str]`: у каждого модуля есть `README.md`, изменённый модуль
  обновляет свою строку в `docs/STATUS.md`. Запуск: `scripts/check_docs.py --staged` или `--all`.
- `deps_check`: сверка таблиц `docs/DEPENDENCIES.md` с lock-файлами. `python_direct(pyproject, group=None)` и
  `npm_direct(package_json, dev=False)` дают прямые зависимости; `uv_locked(uv_lock)` и `bun_locked(bun_lock)` дают
  закреплённые версии; `documented(doc, heading) -> dict[str, str]` читает имя в обратных кавычках и версию из строк
  таблиц раздела `## heading`; `mismatches(rows, expected) -> list[str]` называет пропущенные строки, лишние строки
  и другие версии; `normalize(name)` приводит имя пакета Python к виду PEP 503 без extras.

## Инварианты
- Функции принимают текст или путь и не ходят в сеть; `deps_check` не импортирует проверяемые пакеты, а читает
  только `pyproject.toml`, `uv.lock`, `frontend/package.json`, `frontend/bun.lock`.
- Модуль `devtools` сам исключён из проверки честности и из требования README в `docs_check`.

## Как тестировать
- `uv run pytest tests/unit/test_honesty.py tests/unit/test_docs_check.py tests/unit/test_deps_check.py -q`.
- `test_deps_check.py` сверяет настоящий `docs/DEPENDENCIES.md` с настоящими lock-файлами: смена версии в
  `uv.lock` или `frontend/bun.lock` без правки документа роняет тест.

## Чего не делать
- Не сверять таблицы с установленным окружением: источник версий только lock-файлы.
