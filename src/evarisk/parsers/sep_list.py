import html as htmllib
import re
from dataclasses import dataclass
from datetime import UTC, datetime

_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
_STAMP = re.compile(r"^(\d{4})\s+(\d{2})/(\d{2})\s+(\d{2})(\d{2})$")
_NUMBER = re.compile(r"\d+(\.\d+)?")
_CELLS_PER_EVENT = 10


@dataclass(frozen=True)
class SepEvent:
    begin: datetime
    maximum: datetime
    peak_pfu: float


def _text(cell: str) -> str:
    return " ".join(htmllib.unescape(_TAG.sub(" ", cell)).split())


def _stamp(text: str) -> datetime | None:
    m = _STAMP.match(text)
    if m is None:
        return None
    y, mo, d, hh, mm = (int(g) for g in m.groups())
    return datetime(y, mo, d, hh, mm, tzinfo=UTC)


def parse_sep_page(page: str) -> list[SepEvent]:
    events: list[SepEvent] = []
    for row in _ROW.findall(page):
        cells = [_text(c) for c in _CELL.findall(row)]
        i = 0
        while i + 2 < len(cells):
            begin = _stamp(cells[i])
            maximum = _stamp(cells[i + 1])
            peak_text = cells[i + 2]
            if begin is not None and maximum is not None and _NUMBER.fullmatch(peak_text):
                events.append(SepEvent(begin=begin, maximum=maximum, peak_pfu=float(peak_text)))
                i += _CELLS_PER_EVENT
            else:
                i += 1
    if not events:
        raise ValueError("на странице нет строк таблицы протонных событий")
    return sorted(events, key=lambda e: e.begin)
