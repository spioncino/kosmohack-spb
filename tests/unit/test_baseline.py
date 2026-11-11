from datetime import UTC, datetime
from pathlib import Path

from evarisk.api.schemas import AssessmentRequest
from evarisk.parsers.donki import parse_notifications
from evarisk.parsers.swpc_text import issued_at
from evarisk.replay.baseline import persistence, single_alert, swpc_outlook
from evarisk.store.raw import RawStore

F = datetime(2026, 9, 18, tzinfo=UTC)
FIX = Path("tests/fixtures/swpc_archive")
# Файл GOES за 10 мая доступен с 11 мая 00:05 (D19/D22); файла за 9 мая в фикстурах нет.
CUTOFF_BEFORE_FILE = datetime(2024, 5, 10, 12, 30, tzinfo=UTC)
CUTOFF_AFTER_FILE = datetime(2024, 5, 11, 2, 0, tzinfo=UTC)


def request(cutoff: datetime) -> AssessmentRequest:
    return AssessmentRequest(
        mode="replay",
        cutoff=cutoff,
        eva_start=datetime(2024, 5, 11, 4, tzinfo=UTC),
        duration_hours=6,
        search_start=datetime(2024, 5, 11, 0, tzinfo=UTC),
        search_end=datetime(2024, 5, 11, 14, tzinfo=UTC),
    )


def put_swpc(store: RawStore, name: str, product: str, version: str) -> None:
    text = (FIX / name).read_text(encoding="utf-8")
    store.put(
        source="swpc_archive",
        product=product,
        url=f"https://x.invalid/{name}",
        content=text.encode("utf-8"),
        fetched_at=F,
        published_at=issued_at(text),
        published_at_kind="exact",
        content_type="text/plain",
        data_version=version,
    )


def seed(tmp_path: Path) -> RawStore:
    store = RawStore(tmp_path)
    store.put(
        source="goes_sgps",
        product="sgps_l2_avg5m",
        url="https://x.invalid/20240510.nc",
        content=Path("tests/fixtures/goes/sci_sgps-l2-avg5m_g18_d20240510_v3-0-2.nc").read_bytes(),
        fetched_at=F,
        published_at=datetime(2024, 5, 11, 0, 5, tzinfo=UTC),
        published_at_kind="derived",
        content_type="application/x-netcdf",
        data_version="reprocessed v3-0-2",
    )
    put_swpc(store, "202405100030three_day_forecast.txt", "3day_forecast", "issue 0030")
    put_swpc(store, "202405101230three_day_forecast.txt", "3day_forecast", "issue 1230")
    donki = Path("tests/fixtures/donki/notifications_2024-05-09_2024-05-12.json").read_bytes()
    store.put(
        source="donki",
        product="notifications",
        url="https://x.invalid/donki.json",
        content=donki,
        fetched_at=F,
        published_at=max(n.issued_at for n in parse_notifications(donki)),
        published_at_kind="derived",
        content_type="application/json",
        data_version="notifications",
    )
    return store


def test_persistence_without_available_sample_is_missing_not_quiet(tmp_path: Path) -> None:
    b = persistence(request(CUTOFF_BEFORE_FILE), seed(tmp_path))
    assert b.name == "persistence" and b.level == "unknown"
    assert b.line.availability == "missing" and b.line.intervals[0].level == "unknown"
    assert b.recommendation.insufficient is True and b.recommendation.preferred is None
    assert "нет отсчёта" in b.basis


def test_persistence_spreads_last_sample_over_the_whole_period(tmp_path: Path) -> None:
    b = persistence(request(CUTOFF_AFTER_FILE), seed(tmp_path))
    # Событие 10 мая (208 pfu в 17:45) к 23:55 ещё не опустилось ниже S1: 11 мая 02:10 список NOAA открывает
    # новое событие, а поток ≥10 MeV между ними не падал ниже порога.
    assert b.level in {"elevated", "high"} and "2024-05-10 23:55" in b.basis and "давность 2.1 ч" in b.basis
    assert len(b.line.intervals) == 1
    iv = b.line.intervals[0]
    assert iv.start == datetime(2024, 5, 11, 0, tzinfo=UTC) and iv.end == datetime(2024, 5, 11, 14, tzinfo=UTC)
    assert iv.kind == "computed" and {p.source for p in iv.provenance} == {"goes_sgps"}
    assert b.recommendation.preferred is not None and b.recommendation.preferred.start == datetime(
        2024, 5, 11, 4, tzinfo=UTC
    )
    assert "перенос не улучшает" in b.recommendation.reason
    # D80: восемь окон со стартом 00:00–01:45 раньше отсечения 02:00 исключены, остальные равнозначны.
    past = [s for s in b.windows if s.window.start < CUTOFF_AFTER_FILE]
    assert len(past) == 8 and {s.excluded_reason for s in past} == {"окно начинается раньше момента отсечения"}
    assert len(b.recommendation.equivalents) == len(b.windows) - len(past) - 1


def test_single_alert_takes_the_latest_statement(tmp_path: Path) -> None:
    store = seed(tmp_path)
    before = single_alert(request(CUTOFF_BEFORE_FILE), store)
    # Выпуск 3-day forecast 10 мая 12:30: «below S-scale storm level thresholds».
    assert before.level == "none" and "2024-05-10 12:30" in before.basis and "below" in before.basis
    assert before.recommendation.preferred is not None
    after = single_alert(request(CUTOFF_AFTER_FILE), store)
    # DONKI 20240510-AL-004 (13:46 UTC) и AL-005 (14:12 UTC): поток >10 MeV на GOES выше 10 pfu с 13:35.
    assert after.level == "elevated" and "DONKI SEP 20240510-AL-005" in after.basis
    assert after.line.availability == "available" and any("нет отбоя" in lim for lim in after.line.limitations)
    assert {p.source for p in after.provenance} == {"donki"}


def test_single_alert_without_statement_is_missing(tmp_path: Path) -> None:
    b = single_alert(request(datetime(2024, 5, 9, 0, 0, tzinfo=UTC)), seed(tmp_path))
    assert b.level == "unknown" and b.line.availability == "missing" and b.recommendation.insufficient is True


def test_swpc_outlook_applies_the_latest_probability_to_the_whole_day(tmp_path: Path) -> None:
    store = seed(tmp_path)
    # 3-day forecast 10 мая 12:30: «S1 or greater 55% 55% 55%» на 10–12 мая; выпуск 00:30: 50 % на те же сутки.
    after = swpc_outlook(request(CUTOFF_BEFORE_FILE), store)
    assert after.name == "swpc_outlook" and after.title == "прогноз SWPC без орбиты"
    assert after.level == "elevated" and "вероятность 55 %" in after.basis and "давность 0.0 ч" in after.basis
    assert len(after.line.intervals) == 1 and after.line.intervals[0].kind == "external_forecast"
    assert [p.data_version for p in after.provenance] == ["issue 1230"]
    early = swpc_outlook(request(datetime(2024, 5, 10, 6, 0, tzinfo=UTC)), store)
    # Порог 50 % включителен (proton_outlook_threshold_percent, D39): 50 % выпуска 00:30 даёт elevated.
    assert early.level == "elevated" and "вероятность 50 %" in early.basis
    assert [p.data_version for p in early.provenance] == ["issue 0030"]
    # Одна вероятность на сутки не различает окна: все кандидаты равнозначны, запрошенное окно сохраняется.
    assert after.recommendation.preferred is not None
    assert after.recommendation.preferred.start == datetime(2024, 5, 11, 4, tzinfo=UTC)


def test_swpc_outlook_day_without_issue_is_unknown_not_quiet(tmp_path: Path) -> None:
    req = AssessmentRequest(
        mode="replay",
        cutoff=CUTOFF_BEFORE_FILE,
        eva_start=datetime(2024, 5, 12, 14, tzinfo=UTC),
        duration_hours=6,
        search_start=datetime(2024, 5, 12, 12, tzinfo=UTC),
        search_end=datetime(2024, 5, 13, 12, tzinfo=UTC),
    )
    b = swpc_outlook(req, seed(tmp_path))
    # Выпуск 12:30 называет 10–12 мая; 13 мая в нём нет, поэтому эти сутки unknown, а не none.
    levels = [(iv.start.day, iv.level) for iv in b.line.intervals]
    assert levels == [(12, "elevated"), (13, "unknown")]
    assert "2024-05-13: нет выпуска" in b.basis
    empty = swpc_outlook(request(datetime(2024, 5, 9, 0, 0, tzinfo=UTC)), seed(tmp_path / "empty"))
    assert empty.level == "unknown" and empty.line.availability == "missing"
    assert empty.recommendation.insufficient is True
