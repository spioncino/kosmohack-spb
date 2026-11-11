# orbit

Назначение: разбор орбитальных данных МКС и расчёт положения. В Плане 1 парсер OEM (S4) и траектория из OEM с геодезическими, магнитными координатами и освещённостью (S5). SGP4 и сверка OEM с GP в Плане 2.

## Интерфейс
- `oem.parse_oem(text) -> OemEphemeris`: заголовок `OemHeader` и векторы состояния J2000 (`r_km` в км, `v_kms` в км/с, формы `(N,3)`), `t_s` секунды от `START_TIME` формы `(N,)`, `epoch0 == header.start_time`.
- `oem.creation_date(content: bytes) -> datetime`: время публикации файла из строки `CREATION_DATE` заголовка.
- `interp.hermite_sample(eph, times) -> (r_km (N,3), v_kms (N,3))`: кубический Эрмит по соседним узлам с использованием скоростей; время вне `[start_time, stop_time]` даёт `ValueError`.
- `frames.geodetic_from_gcrs(times, r_km) -> (lat_deg, lon_deg, alt_km)`: широта °, долгота ° в `(-180, 180]`, высота км над эллипсоидом WGS84 через skyfield.
- `sun.sun_direction_gcrs(t) -> (3,)`: единичный вектор на Солнце в экваториальной системе даты по USNO «Approximate Solar Coordinates» (точность около 0.01°). `sun.is_sunlit(r_km, sun_dir) -> bool`: цилиндрическая тень Земли радиусом 6371 км. `sun.solar_longitude_deg(t) -> float`: видимая эклиптическая долгота Солнца в градусах `[0, 360)` по той же формуле (равноденствие даты; от J2000 отличается на прецессию около 0.014°/год).
- `radiant.radiant_unit_gcrs(ra_deg, dec_deg) -> (3,)`: единичный вектор на радиант в экваториальной системе. `radiant.radiant_visible(r_km (N,3), radiant (3,), shield_radius_km) -> NDArray[bool]`: радиант выше опущенного горизонта, то есть угол между направлением «от центра Земли к станции» и радиантом меньше `90° + arccos(shield_radius / |r|)` (около 107.5° на 415 км при сфере 6471 км, D29); точка внутри сферы даёт `ValueError`.
- `magnetic.aacgm(lat_deg, lon_deg, alt_km, t) -> (mlat_deg, mlon_deg, limitations)`: AACGM-v2 по `aacgmv2`, метод `TRACE`; при дате вне покрытия коэффициентов повтор на эпохе 2025-01-01 с ограничением в списке. `magnetic.l_shell(mlat_deg) = 1 / cos²(mlat)`. `magnetic.b_total_nt(lat_deg, lon_deg, alt_km, t)`: модуль поля IGRF по `ppigrf` в нТл. `magnetic.in_saa(b_total_nt, threshold_nt | None)`: без порога все `False`.
- `trajectory.from_oem(eph, provenance, window, step=1 мин, pad=1 ч, is_reconstruction=False) -> Trajectory`: сетка от `window.start - pad` до `window.end + pad`; поля `provider="oem"`, `epoch` равен `CREATION_DATE` файла, `times`, `r_km` (векторы положения GCRS формы `(N,3)` из `hermite_sample`, для геометрии радиантов), `lat_deg`, `lon_deg`, `alt_km`, `sunlit`, `mlat_deg`, `l_shell`, `b_total_nt`, `in_saa`, `limitations`.
- `trajectory.load_trajectory_config(path=config/trajectory.toml) -> TrajectoryConfig`: `saa_b_threshold_nt: float | None`, `sources`.
- `trajectory.saa_passes(trajectory) -> list[SaaPass]`: непрерывные отрезки точек с `in_saa` как проходы через ЮАА: `start`, `end` (время последней точки плюс шаг сетки), `min_b_nt`, `min_at`. На фикстуре 11 мая 2024 это 13 проходов по 4–35 мин, не чаще одного за виток (разрывы 66–85 мин), минимумы 18.9–23.4 тыс. нТл. API оборачивает их в `schemas.SaaPass` с правилом, порогом, источником порога и провенансом эфемериды.

## Системы координат
- OEM в J2000 (EME2000) принимается за GCRS (D27): смещение систем меньше 0.1″, на орбите МКС это единицы метров.
- skyfield переводит GCRS в ITRS с учётом прецессии, нутации и вращения Земли (`Geocentric` + `wgs84.geographic_position_of`); результат геодезический WGS84: широта, долгота, высота над эллипсоидом. Высота над эллипсоидом на широте 51.6° на 13 км больше высоты над сферой 6378.137 км, в которой заданы HA/HP заголовка OEM.
- SGP4 (План 2) даёт TEME и идёт через `EarthSatellite` в тот же `geographic_position_of`; преобразователь один для обоих провайдеров.
- Направление на Солнце считается в экваториальной системе даты; отличие от J2000 в 2024 году около 0.3°, что меньше углового размера полутени для расчёта освещённости.
- Магнитные координаты: AACGM-v2 и IGRF по геодезическим координатам, одна эпоха на интервал не длиннее суток (коэффициенты меняются по годам). Вблизи магнитного экватора AACGM не определён и остаётся `NaN`.

## Инварианты
- Все времена tz-aware UTC; OEM с `TIME_SYSTEM` не `UTC` отвергается `ValueError`.
- Формат не совпал (нет поля заголовка, нет векторов, короткая строка данных, второй сегмент, неубывающие эпохи): `ValueError`, частичный результат не возвращается.
- Файлы OEM содержат запланированные манёвры; при выборе файла для момента T берётся последний доступный к T, где доступность это поздний из `CREATION_DATE` и S3 Last-Modified (D57).
- ЮАА не размечается, пока порог `saa_b_threshold_nt` в `config/trajectory.toml` не сверен человеком: `in_saa` все `False`, в `limitations` строка «ЮАА не размечена: порог поля не сверен (D34)» (D31).
- `NaN` от AACGM не заменяется числом; `l_shell` в таких точках тоже `NaN`.

## Как тестировать
- `uv run pytest tests/unit/test_oem.py tests/physics/test_interp.py tests/physics/test_sun.py tests/physics/test_trajectory.py tests/physics/test_radiant.py -q`; фикстура `tests/fixtures/nasa_oem/2024-05-12_ISS.OEM_J2K_EPH.txt`.
- Ожидаемые значения на 2024-05-11: высота 400–435 км, |широта| до 51.4–51.8°, доля света 0.58–1.0 (день с бета-углом 69.5–69.75° почти полностью освещён), |mlat| ≥ 60°, L в 3.5–9, минимум |B| 15–26 тыс. нТл над Южной Атлантикой; модуль `r_km` 6700–6830 км. Радиант: опущенный горизонт для 6786 км и сферы 6471 км это 17–18°; долгота Солнца 0° в равноденствие 2024-03-20 03:06 и 90° в солнцестояние 2024-06-20 20:51 с точностью 0.05°.

## Чего не делать
- Не смешивать OEM и GP в одном расчёте; провайдер один на задание.
- Не читать данные напрямую из таблиц, только через `AsOf`.
- Не заменять `NaN` магнитных координат нулём и не задавать порог ЮАА в коде.
