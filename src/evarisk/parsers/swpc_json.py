import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from evarisk.timeutil import parse_utc

KpStatus = Literal["observed", "estimated", "predicted"]

_ENERGY = re.compile(r"^>=(\d+(?:\.\d+)?) MeV$")
_KP_STATUSES: tuple[KpStatus, ...] = ("observed", "estimated", "predicted")
_KP_FORECAST_HEADER = ["time_tag", "kp", "observed", "noaa_scale"]


@dataclass(frozen=True)
class IntegralProton:
    time: datetime
    satellite: int
    energy_mev: float
    flux_pfu: float


@dataclass(frozen=True)
class KpForecastRow:
    start: datetime
    kp: float
    status: KpStatus
    noaa_scale: str | None


@dataclass(frozen=True)
class KpMinute:
    time: datetime
    estimated_kp: float


def _rows(content: bytes, name: str) -> list[object]:
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name}: ответ не JSON: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError(f"{name}: ответ не массив: {type(data).__name__}")
    return list(data)


def _record(row: object, name: str, keys: tuple[str, ...]) -> dict[str, object]:
    if not isinstance(row, dict):
        raise ValueError(f"{name}: строка не объект: {row!r}")
    missing = [k for k in keys if k not in row]
    if missing:
        raise ValueError(f"{name}: в строке нет полей {missing}: {row!r}")
    return {str(k): v for k, v in row.items()}


def _number(value: object, field: str, name: str) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ValueError(f"{name}: поле {field} не число: {value!r}")
    return float(value)


def _time(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name}: поле time_tag не строка: {value!r}")
    return parse_utc(value, assume_utc=True)


def _energy_mev(value: object, name: str) -> float:
    if not isinstance(value, str):
        raise ValueError(f"{name}: поле energy не строка: {value!r}")
    m = _ENERGY.match(value)
    if m is None:
        raise ValueError(f"{name}: энергия не вида '>=N MeV': {value!r}")
    return float(m.group(1))


def _status(value: object, name: str) -> KpStatus:
    for status in _KP_STATUSES:
        if value == status:
            return status
    raise ValueError(f"{name}: статус Kp не из {_KP_STATUSES}: {value!r}")


def parse_integral_protons(content: bytes) -> list[IntegralProton]:
    """Интегральные потоки протонов GOES из `integral-protons-7-day.json` SWPC.

    Строка это объект `{time_tag, satellite, flux, energy}`, `energy` вида `>=10 MeV`, `flux` в pfu.
    Времена SWPC в UTC; порядок строк сохраняется.
    """
    name = "integral-protons"
    result: list[IntegralProton] = []
    for row in _rows(content, name):
        rec = _record(row, name, ("time_tag", "satellite", "flux", "energy"))
        satellite = rec["satellite"]
        if not isinstance(satellite, int) or isinstance(satellite, bool):
            raise ValueError(f"{name}: поле satellite не целое: {satellite!r}")
        result.append(
            IntegralProton(
                time=_time(rec["time_tag"], name),
                satellite=satellite,
                energy_mev=_energy_mev(rec["energy"], name),
                flux_pfu=_number(rec["flux"], "flux", name),
            )
        )
    return result


def _kp_forecast_record(row: object, name: str) -> dict[str, object]:
    if isinstance(row, list):
        if len(row) != len(_KP_FORECAST_HEADER):
            raise ValueError(f"{name}: строка из {len(row)} значений вместо {len(_KP_FORECAST_HEADER)}: {row!r}")
        return dict(zip(_KP_FORECAST_HEADER, row, strict=True))
    return _record(row, name, tuple(_KP_FORECAST_HEADER))


def parse_kp_forecast(content: bytes) -> list[KpForecastRow]:
    """Kp по 3-часовым интервалам из `noaa-planetary-k-index-forecast.json` SWPC.

    Строка это объект `{time_tag, kp, observed, noaa_scale}` или, в табличной форме, массив тех же четырёх
    значений после строки заголовка. Поле `observed` содержит статус `observed`, `estimated` или
    `predicted`; `noaa_scale` это код шкалы G или `null`.
    """
    name = "kp-forecast"
    rows = _rows(content, name)
    if rows and rows[0] == _KP_FORECAST_HEADER:
        rows = rows[1:]
    result: list[KpForecastRow] = []
    for row in rows:
        rec = _kp_forecast_record(row, name)
        scale = rec["noaa_scale"]
        if scale is not None and not isinstance(scale, str):
            raise ValueError(f"{name}: поле noaa_scale не строка и не null: {scale!r}")
        result.append(
            KpForecastRow(
                start=_time(rec["time_tag"], name),
                kp=_number(rec["kp"], "kp", name),
                status=_status(rec["observed"], name),
                noaa_scale=scale,
            )
        )
    return result


def parse_kp_1m(content: bytes) -> list[KpMinute]:
    """Оценочный планетарный Kp по минутам из `planetary_k_index_1m.json` SWPC: `time_tag` и `estimated_kp`."""
    name = "kp-1m"
    result: list[KpMinute] = []
    for row in _rows(content, name):
        rec = _record(row, name, ("time_tag", "estimated_kp"))
        result.append(
            KpMinute(time=_time(rec["time_tag"], name), estimated_kp=_number(rec["estimated_kp"], "estimated_kp", name))
        )
    return result
