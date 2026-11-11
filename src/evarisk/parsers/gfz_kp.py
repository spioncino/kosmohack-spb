import json
from dataclasses import dataclass
from datetime import datetime

from evarisk.timeutil import parse_utc

_ARRAYS = ("Kp", "datetime", "status")


@dataclass(frozen=True)
class KpValue:
    start: datetime
    kp: float
    status: str


def parse_kp_json(content: bytes) -> list[KpValue]:
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"ответ GFZ не JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"ответ GFZ не объект: {type(data).__name__}")
    missing = [k for k in _ARRAYS if k not in data]
    if missing:
        raise ValueError(f"в ответе GFZ нет массивов {missing}")
    kps, times, statuses = data["Kp"], data["datetime"], data["status"]
    if not (isinstance(kps, list) and isinstance(times, list) and isinstance(statuses, list)):
        raise ValueError("поля Kp, datetime и status ответа GFZ не массивы")
    if not (len(kps) == len(times) == len(statuses)):
        raise ValueError("массивы Kp, datetime и status разной длины")
    values: list[KpValue] = []
    for t, k, s in zip(times, kps, statuses, strict=True):
        if not isinstance(k, int | float) or isinstance(k, bool):
            raise ValueError(f"значение Kp не число: {k!r} в {t!r}")
        values.append(KpValue(start=parse_utc(str(t)), kp=float(k), status=str(s)))
    return values
