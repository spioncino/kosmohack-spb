import re
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from numpy.typing import NDArray

from evarisk.timeutil import parse_utc

_KV = re.compile(r"^([A-Z_]+)\s*=\s*(.+?)\s*$")
_CREATION = re.compile(rb"^CREATION_DATE\s*=\s*(\S+)", re.M)
_HEADER_FIELDS = ("CREATION_DATE", "ORIGINATOR", "OBJECT_NAME", "REF_FRAME", "TIME_SYSTEM", "START_TIME", "STOP_TIME")


@dataclass(frozen=True)
class OemHeader:
    creation_date: datetime
    originator: str
    object_name: str
    ref_frame: str
    time_system: str
    start_time: datetime
    stop_time: datetime


@dataclass(frozen=True)
class OemEphemeris:
    header: OemHeader
    epoch0: datetime
    t_s: NDArray[np.float64]
    r_km: NDArray[np.float64]
    v_kms: NDArray[np.float64]


def creation_date(content: bytes) -> datetime:
    m = _CREATION.search(content)
    if m is None:
        raise ValueError("в OEM нет CREATION_DATE")
    return parse_utc(m.group(1).decode(), assume_utc=True)


def _state_vector(line: str) -> tuple[datetime, list[float]]:
    parts = line.split()
    if len(parts) < 7:
        raise ValueError(f"строка данных OEM короче семи полей: {line!r}")
    try:
        return parse_utc(parts[0], assume_utc=True), [float(x) for x in parts[1:7]]
    except ValueError as exc:
        raise ValueError(f"нечитаемая строка данных OEM: {line!r}") from exc


def parse_oem(text: str) -> OemEphemeris:
    fields: dict[str, str] = {}
    rows: list[list[float]] = []
    epochs: list[datetime] = []
    in_data = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("COMMENT"):
            continue
        if line == "META_STOP":
            in_data = True
            continue
        if line == "META_START":
            if in_data:
                raise ValueError("OEM с несколькими сегментами не поддерживается")
            continue
        if not in_data:
            kv = _KV.match(line)
            if kv is None:
                raise ValueError(f"нераспознанная строка заголовка OEM: {line!r}")
            fields[kv.group(1)] = kv.group(2)
            continue
        epoch, vector = _state_vector(line)
        epochs.append(epoch)
        rows.append(vector)
    missing = [name for name in _HEADER_FIELDS if name not in fields]
    if missing:
        raise ValueError(f"в заголовке OEM нет полей {missing}")
    if fields["TIME_SYSTEM"] != "UTC":
        raise ValueError(f"неподдерживаемая шкала времени {fields['TIME_SYSTEM']!r}")
    header = OemHeader(
        creation_date=parse_utc(fields["CREATION_DATE"], assume_utc=True),
        originator=fields["ORIGINATOR"],
        object_name=fields["OBJECT_NAME"],
        ref_frame=fields["REF_FRAME"],
        time_system=fields["TIME_SYSTEM"],
        start_time=parse_utc(fields["START_TIME"], assume_utc=True),
        stop_time=parse_utc(fields["STOP_TIME"], assume_utc=True),
    )
    if not rows:
        raise ValueError("в OEM нет векторов состояния")
    data = np.asarray(rows, dtype=np.float64)
    t_s = np.asarray([(e - header.start_time).total_seconds() for e in epochs], dtype=np.float64)
    if np.any(np.diff(t_s) <= 0):
        raise ValueError("эпохи OEM не возрастают строго")
    return OemEphemeris(header=header, epoch0=header.start_time, t_s=t_s, r_km=data[:, :3], v_kms=data[:, 3:])


def oem_availability(created: datetime, last_modified: datetime | None) -> tuple[datetime, str]:
    """Момент, с которого файл эфемериды можно было скачать, и заметка о том, как он получен.

    `CREATION_DATE` это время генерации файла, а не публикации: файл с `CREATION_DATE` 2024-05-10T18:51 появился в
    публичном бакете 2024-05-13T03:06 (S3 Last-Modified). Для строгого replay доступность это позднее из двух
    времён; без Last-Modified известно только время генерации.
    """
    if last_modified is None:
        return created, f"доступность принята по CREATION_DATE {created.isoformat()}: Last-Modified неизвестен"
    available = max(created, last_modified)
    return available, (
        f"доступность по S3 Last-Modified {last_modified.isoformat()}; CREATION_DATE файла {created.isoformat()}"
    )
