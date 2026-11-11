import re
from dataclasses import dataclass
from datetime import UTC, date, datetime

_MONTHS = {
    m: i
    for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)
}
# Выпуски NCEI 2022-2023 годов пишут заголовок без ведущего двоеточия: «Issued: 2023 Jul 17 0030 UTC».
_ISSUED = re.compile(r"^:?Issued:\s+(\d{4}) (\w{3}) (\d{1,2}) (\d{2})(\d{2}) UTC?", re.M)
_DAY_HEADER = re.compile(r"^\s+((?:\w{3} \d{1,2}\s*){3})$", re.M)
_KP_ROW = re.compile(r"^(\d{2})-(\d{2})UT\s+(.+)$", re.M)
# Выпуски 2022 года дают целые Kp в таблице: «00-03UT        2          2          2».
_CELL = re.compile(r"(\d+(?:\.\d+)?)(?:\s*\((G\d)\))?")
_MAX_EXPECTED = re.compile(r"greatest expected 3 hr Kp[^.]*?is (\d+(?:\.\d+)?)", re.S)
_OBSERVED = re.compile(r"greatest observed 3 hr Kp[^.]*?was (\d+(?:\.\d+)?)", re.S)
_PROB_ROW = {
    "s1": re.compile(r"^S1 or greater\s+(\d+)%\s+(\d+)%\s+(\d+)%", re.M),
    "r12": re.compile(r"^R1-R2\s+(\d+)%\s+(\d+)%\s+(\d+)%", re.M),
    "r3": re.compile(r"^R3 or greater\s+(\d+)%\s+(\d+)%\s+(\d+)%", re.M),
}
_KP_SECTION_START = "NOAA Kp index breakdown"
_KP_SECTION_END = "Rationale:"
KP_CELLS_PER_TABLE = 24


def month_number(token: str) -> int:
    month = _MONTHS.get(token)
    if month is None:
        raise ValueError(f"неизвестный месяц {token!r}")
    return month


def issued_at(text: str) -> datetime:
    m = _ISSUED.search(text)
    if m is None:
        raise ValueError("нет строки Issued")
    year, mon, day, hh, mm = m.groups()
    return datetime(int(year), month_number(mon), int(day), int(hh), int(mm), tzinfo=UTC)


@dataclass(frozen=True)
class KpCell:
    day: date
    start_hour: int
    kp: float
    scale: str | None


@dataclass(frozen=True)
class ThreeDayForecast:
    issued_at: datetime
    days: tuple[date, date, date]
    kp: tuple[KpCell, ...]
    max_expected_kp: float
    observed_max_kp_24h: float
    s1_probability: dict[date, int]
    r1_r2_probability: dict[date, int]
    r3_probability: dict[date, int]


def days_from_header(header: str, issued: datetime) -> tuple[date, date, date]:
    tokens = header.split()
    if len(tokens) != 6:
        raise ValueError(f"заголовок дней таблицы Kp не из трёх пар: {header!r}")
    result: list[date] = []
    for mon, day in zip(tokens[0::2], tokens[1::2], strict=True):
        month = month_number(mon)
        year = issued.year + 1 if month < issued.month else issued.year
        result.append(date(year, month, int(day)))
    return result[0], result[1], result[2]


def parse_kp_table(section: str, issued: datetime) -> tuple[tuple[date, date, date], tuple[KpCell, ...]]:
    header = _DAY_HEADER.search(section)
    if header is None:
        raise ValueError("нет заголовка дней в таблице Kp")
    days = days_from_header(header.group(1), issued)
    cells: list[KpCell] = []
    for row in _KP_ROW.finditer(section):
        values = _CELL.findall(row.group(3))
        if len(values) != 3:
            raise ValueError(f"строка Kp с {len(values)} значениями: {row.group(0)!r}")
        for day, (kp, scale) in zip(days, values, strict=True):
            cells.append(KpCell(day=day, start_hour=int(row.group(1)), kp=float(kp), scale=scale or None))
    if len(cells) != KP_CELLS_PER_TABLE:
        raise ValueError(f"в таблице Kp {len(cells)} ячеек вместо {KP_CELLS_PER_TABLE}")
    return days, tuple(cells)


def _kp_section(text: str) -> str:
    start = text.find(_KP_SECTION_START)
    if start < 0:
        raise ValueError(f"нет раздела {_KP_SECTION_START!r}")
    section = text[start + len(_KP_SECTION_START) :]
    end = section.find(_KP_SECTION_END)
    return section if end < 0 else section[:end]


def parse_three_day_forecast(text: str) -> ThreeDayForecast:
    issued = issued_at(text)
    days, cells = parse_kp_table(_kp_section(text), issued)
    max_m = _MAX_EXPECTED.search(text)
    obs_m = _OBSERVED.search(text)
    if max_m is None or obs_m is None:
        raise ValueError("нет строк о наибольшем ожидаемом или наблюдённом Kp")

    def probs(key: str) -> dict[date, int]:
        m = _PROB_ROW[key].search(text)
        if m is None:
            raise ValueError(f"нет строки вероятностей {key}")
        return {d: int(v) for d, v in zip(days, m.groups(), strict=True)}

    return ThreeDayForecast(
        issued_at=issued,
        days=days,
        kp=cells,
        max_expected_kp=float(max_m.group(1)),
        observed_max_kp_24h=float(obs_m.group(1)),
        s1_probability=probs("s1"),
        r1_r2_probability=probs("r12"),
        r3_probability=probs("r3"),
    )
