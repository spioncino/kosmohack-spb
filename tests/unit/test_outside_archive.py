from datetime import UTC, datetime, timedelta
from pathlib import Path

from evarisk.api.service import outside_archive_note
from evarisk.store.raw import RawStore
from evarisk.types import Window

F = datetime(2026, 9, 18, tzinfo=UTC)


def put_day(store: RawStore, available: datetime) -> None:
    store.put(
        source="goes_sgps",
        product="sgps_l2_avg5m",
        url=f"https://x.invalid/{available:%Y%m%d}.nc",
        content=available.isoformat().encode(),
        fetched_at=F,
        published_at=available,
        published_at_kind="derived",
        content_type="application/x-netcdf",
        data_version="reprocessed v3-0-2",
    )


def put_days(store: RawStore, first_available: datetime, count: int) -> None:
    for i in range(count):
        put_day(store, first_available + timedelta(days=i))


def window(day: int, month: int, year: int = 2024) -> Window:
    return Window(start=datetime(year, month, day, 0, tzinfo=UTC), end=datetime(year, month, day, 14, tzinfo=UTC))


def test_period_after_the_last_archive_day_is_named_with_the_covered_range(tmp_path: Path) -> None:
    # Файл за сутки D доступен в D+1 00:05: 63 записи с доступностью от 2 мая покрывают сутки с 1 мая по 2 июля.
    store = RawStore(tmp_path)
    put_days(store, datetime(2024, 5, 2, 0, 5, tzinfo=UTC), 63)
    note = outside_archive_note(store, window(15, 7))
    assert note is not None and "2024-05-01 … 2024-07-02" in note and "рекомендации нет" in note
    assert outside_archive_note(store, window(20, 4)) is not None


def test_period_inside_the_archive_or_an_empty_store_gives_no_note(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    assert outside_archive_note(store, window(15, 7)) is None
    put_days(store, datetime(2024, 5, 2, 0, 5, tzinfo=UTC), 63)
    assert outside_archive_note(store, window(11, 5)) is None and outside_archive_note(store, window(2, 7)) is None


def test_gap_between_two_archive_ranges_is_outside_and_both_ranges_are_named(tmp_path: Path) -> None:
    # Два куска архива: 1 мая – 2 июля 2024 и 1–13 ноября 2025. Июль 2024 лежит между ними и измерениями не покрыт.
    store = RawStore(tmp_path)
    put_days(store, datetime(2024, 5, 2, 0, 5, tzinfo=UTC), 63)
    put_days(store, datetime(2025, 11, 2, 0, 5, tzinfo=UTC), 13)
    note = outside_archive_note(store, window(15, 7))
    assert note is not None and "2024-05-01 … 2024-07-02, 2025-11-01 … 2025-11-13" in note
    assert outside_archive_note(store, window(9, 11, 2025)) is None
