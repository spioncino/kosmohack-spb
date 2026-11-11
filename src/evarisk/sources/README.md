# sources

Назначение: адаптеры источников. Адаптер только скачивает и сохраняет байты в `RawStore` с временем публикации и версией данных, взятыми из самого продукта. Разбор содержимого живёт в `parsers/` и `orbit/`.

## Интерфейс
- `SourceAdapter`: `name`, `products`, `cadence`, `supports_archive`, `fetch_current(fetcher, store)`, `fetch_archive(fetcher, store, start, end)`. Адаптер без архива возвращает из `fetch_archive` пустой список и имеет `supports_archive = False`.
- `HttpFetcher.get(url) -> FetchResult`; повторы при 5xx и ошибках сети, 4xx сразу `FetchError`.
- `HttpFetcher.post(url, data=) -> FetchResult`; форма, без повторов.
- `save(store, source=, product=, result=, published_at=, published_at_kind=, data_version=)` сохраняет результат.
- `registry.adapters()` все адаптеры по имени; новый адаптер регистрируется в `_FACTORIES`.

## Адаптеры текущего режима
| имя | продукты | cadence | published_at | data_version | архив |
|---|---|---|---|---|---|
| `nasa_oem` | `oem` | 6 ч | `CREATION_DATE`, exact | `oem <CREATION_DATE isoformat>` | да, листинг S3 по месяцам |
| `celestrak` | `gp` | 2 ч | fetched_at, derived | `gp latest` | нет |
| `swpc_live` | `3day_forecast`, `kp_1m`, `kp_forecast`, `scales`, `alerts`, `protons_7d`, `instrument_sources` | 5 мин | 3-day по `:Issued:` exact; alerts наибольший `issue_datetime` derived; остальные fetched_at derived | 3-day `issue HHMM`, остальные `live` | нет |
| `donki` | `notifications` | 1 ч | наибольший `messageIssueTime`, derived; пустой ответ unknown | `notifications` | да, отрезки по 7 дней |
| `gfz_kp` | `kp` | 1 ч | конец последнего 3-часового интервала + 1 ч, derived (D18); пустой ответ unknown | `definitive`, если все `status == "def"`, иначе `nowcast`; пустой ответ `empty` | да, один запрос на диапазон |
| `socrates` | `conjunctions_iss` | 6 ч | `Data current as of`, exact | `run <data_as_of isoformat>` | нет |
| `meteor_docs` | `LEO_Forecast_<год>` (PDF) и `meo_flux_<год>` (`flux_data.txt`) для годов реестра `FORECASTS`: 2024, 2025, 2026 | 30 сут | derived: конец дня распространения записи NTRS (2023-11-02, 2024-10-18, 2025-10-17); файлы загружены раньше этой даты | `MEO <год>, NTRS <запись>, distributed <дата>` | нет |

Space-Track и архивные адаптеры GOES и NCEI не реализованы: архив закрыт зеркалом `data/mirror` (D33). CelesTrak опрашивает только воркер по cadence (usage policy).

## Инварианты
- `published_at_kind`: `exact` если продукт содержит время выпуска (`:Issued:`, `messageIssueTime`, `CREATION_DATE`), `derived` если выведено по правилу, описанному в `notes`, `unknown` иначе.
- `data_version` обязателен у каждой записи (D20): номер выпуска, версия файла или дата снимка из самого продукта.
- Адаптер не глотает ошибки: `FetchError` поднимается наверх, вызывающий решает, что делать.
- Один адаптер, один источник; продукты внутри перечислены в `products`.

## Как тестировать
- `uv run pytest tests/sources -q`; сеть подменяется `respx`, ответы берутся из `tests/fixtures/<источник>/`.

## Зеркало архивов (D59)
- `archive_mirror.py`: `Mirror(root, fetch, report)` с манифестом `manifest.jsonl`, `mirror_period(mir, start, end, docs, current_oem)` и функции по источникам (NCEI SWPC, NASA OEM с запасом `OEM_LEAD_DAYS` = 10 суток, GOES-18 SGPS, DONKI кусками по 7 суток, GFZ Kp); `meteor_docs(years)` и `mirror_meteor_forecasts(mir, years)` берут PDF и `flux_data.txt` прогнозов NASA MEO на годы из реестра `meteor_docs.FORECASTS` (файлы `meteors/LEO_Forecast_<год>.pdf` и `meteors/meo_<год>_flux_data.txt`); `mirror_period` с `docs=True` берёт их на годы периода. Сеть только через стандартную библиотеку.
- `meteor_docs.py`: `MeoForecast(year, citation, pdf, distributed)` с адресами NTRS и временем доступности, реестр `FORECASTS` прогнозов с файлом почасовых данных (D67), `flux_product(year)`.
- Загрузка подменяется параметром `fetch: Callable[[str], (status, body, headers)]`: тесты `tests/unit/test_archive_mirror.py` в сеть не ходят.
- Пользователи: `scripts/mirror_archives.py` (ручное зеркало, `make fetch`) и `store/backfill.py` (докачка по запросу).
- `Mirror.get` и `Mirror.listing` исключений не поднимают: любой отказ загрузки (`http.client.IncompleteRead` и прочие `HTTPException`, ошибка службы, отказ сети) и отказ записи файла (`OSError`) дают `None` или пустой список и строку в `Mirror.failed`; зеркалирование остальных файлов продолжается. `http_fetch` повторяет попытку и на `http.client.HTTPException`: обрыв тела ответа это повод повторить, а не отказ.

## Чего не делать
- Не парсить и не нормализовать данные в адаптере.
- Не ставить `published_at = fetched_at` без пометки `derived` и причины.
