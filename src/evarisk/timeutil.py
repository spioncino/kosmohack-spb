from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"наивная дата запрещена: {dt!r}")
    return dt.astimezone(UTC)


def parse_utc(text: str, *, assume_utc: bool = False) -> datetime:
    value = text.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        if not assume_utc:
            raise ValueError(f"строка без зоны: {text!r}")
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)
