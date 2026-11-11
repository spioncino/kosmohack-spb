import html as htmllib
import re
from dataclasses import dataclass
from datetime import UTC, datetime

_MONTHS = {
    m: i
    for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)
}
_STAMP = r"(\d{4}) (\w{3}) (\d{1,2}) (\d{2}):(\d{2}):(\d{2}) UTC"
_AS_OF = re.compile(rf"Data current as of {_STAMP}")
_INTERVAL = re.compile(rf"Start = {_STAMP}, Stop = {_STAMP}")
_THRESHOLD = re.compile(r"Computation Threshold: ([\d.]+) km")
_TABLE = re.compile(r"<table[^>]*>(.*?)</table>", re.S | re.I)
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
_OPS_STATUS = re.compile(r"\s*\[.*?\]\s*$")
_FIRST_ROW_CELLS = 7
_SECOND_ROW_CELLS = 6
_FIRST_ROW_MARK = "GP Data"


@dataclass(frozen=True)
class Conjunction:
    primary_catnr: int
    secondary_catnr: int
    secondary_name: str
    tca: datetime
    min_range_km: float
    rel_speed_kms: float
    max_probability: float
    dilution_km: float


@dataclass(frozen=True)
class SocratesReport:
    data_as_of: datetime
    interval_start: datetime
    interval_stop: datetime
    threshold_km: float
    conjunctions: list[Conjunction]


def _month(token: str) -> int:
    month = _MONTHS.get(token)
    if month is None:
        raise ValueError(f"неизвестный месяц {token!r}")
    return month


def _dt(parts: tuple[str, ...]) -> datetime:
    y, mon, d, hh, mm, ss = parts
    return datetime(int(y), _month(mon), int(d), int(hh), int(mm), int(ss), tzinfo=UTC)


def data_as_of(html: str) -> datetime:
    m = _AS_OF.search(html)
    if m is None:
        raise ValueError("нет строки Data current as of")
    return _dt(m.groups())


def _cells(row: str) -> list[str]:
    return [htmllib.unescape(_TAG.sub("", c)).strip() for c in _CELL.findall(row)]


def _clean_name(name: str) -> str:
    return _OPS_STATUS.sub("", name).strip()


def _conjunction(first: list[str], second: list[str]) -> Conjunction:
    return Conjunction(
        primary_catnr=int(first[1]),
        secondary_catnr=int(second[1]),
        secondary_name=_clean_name(second[2]),
        tca=datetime.strptime(first[4], "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=UTC),
        min_range_km=float(first[5]),
        rel_speed_kms=float(first[6]),
        max_probability=float(second[4]),
        dilution_km=float(second[5]),
    )


def parse_socrates(html: str) -> SocratesReport:
    interval = _INTERVAL.search(html)
    threshold = _THRESHOLD.search(html)
    if interval is None or threshold is None:
        raise ValueError("нет строк интервала или порога расчёта")
    table = next((t for t in _TABLE.findall(html) if "TCA" in t), None)
    if table is None:
        raise ValueError("нет таблицы сближений")
    rows = [_cells(r) for r in _ROW.findall(table)]
    conjunctions: list[Conjunction] = []
    i = 0
    while i < len(rows) - 1:
        first, second = rows[i], rows[i + 1]
        if len(first) == _FIRST_ROW_CELLS and first[0] == _FIRST_ROW_MARK:
            if len(second) != _SECOND_ROW_CELLS:
                raise ValueError(f"вторая строка сближения из {len(second)} ячеек вместо {_SECOND_ROW_CELLS}")
            conjunctions.append(_conjunction(first, second))
            i += 2
        else:
            i += 1
    g = interval.groups()
    return SocratesReport(
        data_as_of=data_as_of(html),
        interval_start=_dt(g[:6]),
        interval_stop=_dt(g[6:]),
        threshold_km=float(threshold.group(1)),
        conjunctions=conjunctions,
    )
