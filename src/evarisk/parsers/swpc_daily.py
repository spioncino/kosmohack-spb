import re
from dataclasses import dataclass
from datetime import date, datetime

from evarisk.parsers.swpc_text import KpCell, issued_at, month_number, parse_kp_table

_AP_LINE = re.compile(r"^(Observed|Estimated) Ap (\d{2}) (\w{3}) (\d{3})\s*$", re.M)
_GEOMAG_KP_SECTION = "NOAA Kp index forecast"
_PREDICTION_DATES = re.compile(
    r"^:Prediction_dates:\s+(\d{4}) (\w{3}) (\d{1,2})\s+(\d{4}) (\w{3}) (\d{1,2})\s+(\d{4}) (\w{3}) (\d{1,2})\s*$", re.M
)
_K_ROW = re.compile(r"^(Mid|High)/(\d{2})-(\d{2})UT\s+(\d+)\s+(\d+)\s+(\d+)\s*$", re.M)
_PROTON_ROW = re.compile(r"^Proton\s+(\d+)\s+(\d+)\s+(\d+)\s*$", re.M)
_ROWS_PER_DAY = 8


@dataclass(frozen=True)
class GeomagForecast:
    issued_at: datetime
    days: tuple[date, date, date]
    observed_ap: tuple[date, int] | None
    estimated_ap: tuple[date, int] | None
    kp: tuple[KpCell, ...]


@dataclass(frozen=True)
class Daypre:
    issued_at: datetime
    days: tuple[date, date, date]
    mid_k: tuple[KpCell, ...]
    high_k: tuple[KpCell, ...]
    proton_probability: dict[date, int]


def _date_nearest(issued: datetime, month: int, day: int) -> date:
    """Ближайшая к выпуску дата с таким днём и месяцем; 29 февраля существует не в каждом соседнем году."""
    candidates: list[date] = []
    for shift in (-1, 0, 1):
        try:
            candidates.append(date(issued.year + shift, month, day))
        except ValueError:
            continue
    if not candidates:
        raise ValueError(f"нет даты {day:02d}.{month:02d} в годах около {issued.year}")
    return min(candidates, key=lambda d: abs((d - issued.date()).days))


def _ap_line(text: str, label: str, issued: datetime) -> tuple[date, int] | None:
    for m in _AP_LINE.finditer(text):
        if m.group(1) == label:
            return _date_nearest(issued, month_number(m.group(3)), int(m.group(2))), int(m.group(4))
    return None


def parse_geomag_forecast(text: str) -> GeomagForecast:
    issued = issued_at(text)
    start = text.find(_GEOMAG_KP_SECTION)
    if start < 0:
        raise ValueError(f"нет раздела {_GEOMAG_KP_SECTION!r}")
    days, cells = parse_kp_table(text[start + len(_GEOMAG_KP_SECTION) :], issued)
    return GeomagForecast(
        issued_at=issued,
        days=days,
        observed_ap=_ap_line(text, "Observed", issued),
        estimated_ap=_ap_line(text, "Estimated", issued),
        kp=cells,
    )


def _prediction_days(text: str) -> tuple[date, date, date]:
    m = _PREDICTION_DATES.search(text)
    if m is None:
        raise ValueError("нет строки :Prediction_dates:")
    g = m.groups()
    result = [date(int(g[i]), month_number(g[i + 1]), int(g[i + 2])) for i in (0, 3, 6)]
    return result[0], result[1], result[2]


def _k_rows(text: str, latitude: str, days: tuple[date, date, date]) -> tuple[KpCell, ...]:
    cells: list[KpCell] = []
    for row in _K_ROW.finditer(text):
        if row.group(1) != latitude:
            continue
        for day, value in zip(days, row.groups()[3:], strict=True):
            cells.append(KpCell(day=day, start_hour=int(row.group(2)), kp=float(value), scale=None))
    expected = _ROWS_PER_DAY * len(days)
    if len(cells) != expected:
        raise ValueError(f"в таблице K ({latitude}) {len(cells)} ячеек вместо {expected}")
    return tuple(cells)


def parse_daypre(text: str) -> Daypre:
    issued = issued_at(text)
    days = _prediction_days(text)
    proton = _PROTON_ROW.search(text)
    if proton is None:
        raise ValueError("нет строки Proton в :Whole_Disk_Flare_Prob:")
    return Daypre(
        issued_at=issued,
        days=days,
        mid_k=_k_rows(text, "Mid", days),
        high_k=_k_rows(text, "High", days),
        proton_probability={d: int(v) for d, v in zip(days, proton.groups(), strict=True)},
    )
