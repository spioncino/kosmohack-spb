from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from evarisk.replay.baseline import PERSISTENCE_LOOKBACK
from evarisk.sources.archive_mirror import GOES
from evarisk.sources.meteor_docs import FORECASTS
from evarisk.store.backfill import (
    REPLAY_LEAD_DAYS,
    backfill,
    covered_days,
    missing_days,
    missing_forecast_years,
    needed_days,
)
from evarisk.store.raw import RawStore
from evarisk.types import Window

NOW = datetime(2026, 9, 19, 5, 0, tzinfo=UTC)
JULY = Window(start=datetime(2024, 7, 15, 0, tzinfo=UTC), end=datetime(2024, 7, 16, 0, tzinfo=UTC))
NAMES = [f"sci_sgps-l2-avg5m_g18_d202407{day:02d}_v3-0-2.nc" for day in (11, 12, 13, 14)]
MEO_2024 = FORECASTS[2024]
FLUX = Path("tests/fixtures/meo/flux_data_2024_excerpt.txt")


class FakeArchive:
    """Подменённый архив: листинг GOES за июль 2024 с файлами за 11–14 июля и прогноз NASA MEO на 2024 год
    (PDF и flux_data.txt), на остальное 404; сеть не трогается."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, url: str) -> tuple[int, bytes, dict[str, str]]:
        self.urls.append(url)
        if url == f"{GOES}/2024/07/":
            body = "".join(f'<a href="{name}">{name}</a>' for name in NAMES)
            return 200, body.encode(), {"content-type": "text/html"}
        if url.startswith(f"{GOES}/2024/07/sci_sgps"):
            return 200, url.encode(), {"content-type": "application/x-netcdf"}
        if url == MEO_2024.pdf_url:
            return 200, b"%PDF-1.5 stub", {"content-type": "application/pdf"}
        if url == MEO_2024.flux_url:
            return 200, FLUX.read_bytes(), {"content-type": "text/plain"}
        return 404, b"", {}


def no_network(url: str) -> tuple[int, bytes, dict[str, str]]:
    raise AssertionError(f"сеть не должна вызываться: {url}")


def broken_network(url: str) -> tuple[int, bytes, dict[str, str]]:
    raise RuntimeError(f"{url}: URLError: сеть недоступна")


def quiet(_: str) -> None:
    return None


def test_needed_days_start_ten_days_before_cutoff_and_end_with_the_search() -> None:
    # В replay сутки берутся за 10 суток до отсечения: столько просматривает бейзлайн персистентности.
    nov9 = Window(start=datetime(2025, 11, 9, 0, tzinfo=UTC), end=datetime(2025, 11, 10, 0, tzinfo=UTC))
    replay = needed_days(nov9, datetime(2025, 11, 8, 12, tzinfo=UTC), NOW.date())
    assert (replay[0], replay[-1], len(replay)) == (date(2025, 10, 29), date(2025, 11, 9), 12)
    assert timedelta(days=REPLAY_LEAD_DAYS) == PERSISTENCE_LOOKBACK
    review = needed_days(nov9, None, NOW.date())
    assert (review[0], review[-1]) == (date(2025, 11, 6), date(2025, 11, 9))


def test_days_whose_daily_file_is_not_published_yet_are_not_requested() -> None:
    # Файл за сутки D выходит в D+1, запас ещё сутки: 10 ноября запрашиваются сутки не позже 8 ноября.
    nov9 = Window(start=datetime(2025, 11, 9, 0, tzinfo=UTC), end=datetime(2025, 11, 10, 0, tzinfo=UTC))
    days = needed_days(nov9, None, date(2025, 11, 10))
    assert days[-1] == date(2025, 11, 8)
    assert needed_days(nov9, None, date(2025, 11, 1)) == []


def test_backfill_fetches_missing_days_and_names_days_absent_in_the_archive(tmp_path: Path) -> None:
    # Нужны сутки 12–15 июля; архив отдаёт файлы за 11–14 июля: 11-е вне периода, за 15-е файла нет.
    store = RawStore(tmp_path / "raw")
    archive = FakeArchive()
    report = backfill(store, tmp_path / "mirror", JULY, None, NOW, fetch=archive, report=quiet)
    assert report is not None
    assert (report.start, report.end) == (date(2024, 7, 12), date(2024, 7, 15))
    # Три суточных файла GOES и прогноз MEO на 2024 год (PDF и почасовые данные).
    assert (report.new_files, report.imported) == (5, 5)
    assert report.still_missing == (date(2024, 7, 15),)
    # Правило импорта зеркала: файл за сутки D доступен в D+1 00:05.
    published = sorted(r.published_at for r in store.records("goes_sgps") if r.published_at is not None)
    assert published == [datetime(2024, 7, day, 0, 5, tzinfo=UTC) for day in (13, 14, 15)]
    notes = " | ".join(report.notes())
    assert "2024-07-12 … 2024-07-15" in notes and "новых файлов 5" in notes
    assert store.records(source="meteor_docs", product="meo_flux_2024")
    assert "нет суточных файлов потока протонов за 2024-07-15 … 2024-07-15" in notes


def test_covered_period_needs_no_network_and_second_backfill_fetches_only_the_gap(tmp_path: Path) -> None:
    store = RawStore(tmp_path / "raw")
    backfill(store, tmp_path / "mirror", JULY, None, NOW, fetch=FakeArchive(), report=quiet)
    covered = Window(start=datetime(2024, 7, 14, 0, tzinfo=UTC), end=datetime(2024, 7, 15, 0, tzinfo=UTC))
    # Для окна 14 июля нужны сутки 11–14: после первой докачки не хватает только 11 июля.
    assert missing_days(store, covered, None, NOW.date()) == [date(2024, 7, 11)]
    archive = FakeArchive()
    report = backfill(store, tmp_path / "mirror", covered, None, NOW, fetch=archive, report=quiet)
    assert report is not None and (report.start, report.end, report.new_files) == (date(2024, 7, 11),) * 2 + (1,)
    assert backfill(store, tmp_path / "mirror", covered, None, NOW, fetch=no_network, report=quiet) is None


def test_network_failure_is_reported_and_does_not_raise(tmp_path: Path) -> None:
    store = RawStore(tmp_path / "raw")
    report = backfill(store, tmp_path / "mirror", JULY, None, NOW, fetch=broken_network, report=quiet)
    assert report is not None and report.new_files == 0 and report.imported == 0
    assert report.failed and "сеть недоступна" in report.failed[0]
    assert report.still_missing == tuple(date(2024, 7, day) for day in (12, 13, 14, 15))
    assert any("ошибок загрузки" in note for note in report.notes())


def test_meo_forecast_is_fetched_even_when_proton_days_are_covered(tmp_path: Path) -> None:
    # Хранилище с потоком протонов, но без прогноза MEO на год окна: докачка берёт только прогноз (D67).
    store = RawStore(tmp_path / "raw")
    backfill(store, tmp_path / "mirror", JULY, None, NOW, fetch=FakeArchive(), report=quiet)
    for record in store.records(source="meteor_docs"):
        assert record.product in {"meo_flux_2024", "LEO_Forecast_2024"}
    bare = RawStore(tmp_path / "bare")
    covered = Window(start=datetime(2024, 7, 14, 0, tzinfo=UTC), end=datetime(2024, 7, 15, 0, tzinfo=UTC))
    assert missing_forecast_years(bare, covered) == {2024}
    # Годы без машиночитаемого прогноза в NTRS (2023) не запрашиваются: линия ответит «нет данных».
    old = Window(start=datetime(2023, 7, 14, 0, tzinfo=UTC), end=datetime(2023, 7, 15, 0, tzinfo=UTC))
    assert missing_forecast_years(bare, old) == set()
    assert missing_forecast_years(store, covered) == set()


def test_covered_days_come_from_the_file_name_not_from_the_late_availability(tmp_path: Path) -> None:
    # Файл за 10 мая 2024 появился на NCEI 17 мая 05:06:54 (Last-Modified): покрыты сутки 10 мая, а не 16 мая.
    store = RawStore(tmp_path)
    for url, available in (
        (f"{GOES}/2024/05/sci_sgps-l2-avg5m_g18_d20240510_v3-0-2.nc", datetime(2024, 5, 17, 5, 6, 54, tzinfo=UTC)),
        ("https://x.invalid/day.nc", datetime(2024, 6, 2, 0, 5, tzinfo=UTC)),
    ):
        store.put(
            source="goes_sgps",
            product="sgps_l2_avg5m",
            url=url,
            content=url.encode(),
            fetched_at=NOW,
            published_at=available,
            published_at_kind="derived",
            content_type="application/x-netcdf",
            data_version="reprocessed v3-0-2",
        )
    # Имя без суток относит файл к дате доступности минус один день.
    assert covered_days(store) == {date(2024, 5, 10), date(2024, 6, 1)}
