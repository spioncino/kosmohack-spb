from datetime import UTC, datetime, timedelta
from pathlib import Path

from evarisk.orbit.oem import parse_oem
from evarisk.orbit.trajectory import Trajectory, from_oem
from evarisk.parsers.donki import parse_notifications
from evarisk.parsers.swpc_text import issued_at
from evarisk.risk.config import load_sep_config
from evarisk.risk.lines.sep import (
    CONFIDENCE_DONKI,
    CONFIDENCE_OUTLOOK_ONLY,
    LIMIT_CME_KP,
    LIMIT_DONKI_STATE,
    LIMIT_NO_DONKI,
    SepLine,
)
from evarisk.store.asof import AsOf
from evarisk.store.raw import RawStore
from evarisk.store.views import provenance_of
from evarisk.types import Interval, LineAssessment, Window

F = datetime(2026, 9, 18, tzinfo=UTC)
FIX = Path("tests/fixtures/swpc_archive")
WINDOW = Window(start=datetime(2024, 5, 11, 4, 0, tzinfo=UTC), end=datetime(2024, 5, 11, 10, 0, tzinfo=UTC))
WINDOW_0900 = datetime(2024, 5, 11, 9, 0, tzinfo=UTC)
CUTOFF_MAY_9 = datetime(2024, 5, 9, 0, 0, tzinfo=UTC)
CUTOFF_MAY_10_1230 = datetime(2024, 5, 10, 12, 30, tzinfo=UTC)
CUTOFF_MAY_10_1600 = datetime(2024, 5, 10, 16, 0, tzinfo=UTC)
CUTOFF_MAY_11_0200 = datetime(2024, 5, 11, 2, 0, tzinfo=UTC)
CUTOFF_MAY_11_0300 = datetime(2024, 5, 11, 3, 0, tzinfo=UTC)
# Эфемерида фикстуры начинается 2024-05-10T12:00Z, окно вечера 10 мая лежит внутри неё.
WINDOW_MAY_10_EVENING = Window(
    start=datetime(2024, 5, 10, 16, 0, tzinfo=UTC), end=datetime(2024, 5, 10, 22, 0, tzinfo=UTC)
)
# D60: последнее подтверждение события ≥10 MeV с 13:35 это сообщение 14:12 (20240510-AL-005), удержание 48 ч.
DONKI_HOLD_END_MAY_10 = datetime(2024, 5, 12, 14, 12, tzinfo=UTC)
# D60: последнее подтверждение событий с 02:10 это сообщение 02:46 (20240511-AL-007); удержание ≥100 MeV 24 ч.
DONKI_P100_HOLD_END = datetime(2024, 5, 12, 2, 46, tzinfo=UTC)
WINDOW_MAY_11_NIGHT = Window(start=datetime(2024, 5, 11, 0, 0, tzinfo=UTC), end=datetime(2024, 5, 11, 4, 0, tzinfo=UTC))
WINDOW_MAY_12 = Window(start=datetime(2024, 5, 12, 4, 0, tzinfo=UTC), end=datetime(2024, 5, 12, 10, 0, tzinfo=UTC))
WINDOW_JUNE_21 = Window(start=datetime(2024, 6, 21, 4, 0, tzinfo=UTC), end=datetime(2024, 6, 21, 10, 0, tzinfo=UTC))
CUTOFF_JUNE_20_0100 = datetime(2024, 6, 20, 1, 0, tzinfo=UTC)


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
    for day in ("20240510", "20240511"):
        store.put(
            source="goes_sgps",
            product="sgps_l2_avg5m",
            url=f"https://x.invalid/{day}.nc",
            content=Path(f"tests/fixtures/goes/sci_sgps-l2-avg5m_g18_d{day}_v3-0-2.nc").read_bytes(),
            fetched_at=F,
            published_at=datetime(2024, 5, int(day[6:]) + 1, 0, 5, tzinfo=UTC),
            published_at_kind="derived",
            content_type="application/x-netcdf",
            data_version="reprocessed v3-0-2",
        )
    store.put(
        source="gfz_kp",
        product="kp",
        url="https://x.invalid/kp.json",
        content=Path("tests/fixtures/gfz/kp_2024-05-10_11.json").read_bytes(),
        fetched_at=F,
        published_at=datetime(2024, 7, 15, tzinfo=UTC),
        published_at_kind="derived",
        content_type="application/json",
        data_version="definitive",
    )
    store.put(
        source="nasa_oem",
        product="oem",
        url="https://x.invalid/oem.txt",
        content=Path("tests/fixtures/nasa_oem/2024-05-12_ISS.OEM_J2K_EPH.txt").read_bytes(),
        fetched_at=F,
        published_at=datetime(2024, 5, 10, 18, 51, 53, tzinfo=UTC),
        published_at_kind="exact",
        content_type="text/plain",
        data_version="oem 2024-05-10",
    )
    put_swpc(store, "202405100030three_day_forecast.txt", "3day_forecast", "issue 0030")
    put_swpc(store, "202405101230three_day_forecast.txt", "3day_forecast", "issue 1230")
    put_swpc(store, "20240508geomag_forecast.txt", "geomag_forecast", "daily")
    put_swpc(store, "20240509geomag_forecast.txt", "geomag_forecast", "daily")
    put_swpc(store, "20240510geomag_forecast.txt", "geomag_forecast", "daily")
    put_swpc(store, "20240510daypre.txt", "daypre", "daily")
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


def seed_june(tmp_path: Path) -> RawStore:
    store = RawStore(tmp_path)
    store.put(
        source="nasa_oem",
        product="oem",
        url="https://x.invalid/oem_june.txt",
        content=Path("tests/fixtures/nasa_oem/2024-06-18_ISS.OEM_J2K_EPH.txt").read_bytes(),
        fetched_at=F,
        published_at=datetime(2024, 6, 18, 3, 23, 22, tzinfo=UTC),
        published_at_kind="exact",
        content_type="text/plain",
        data_version="oem 2024-06-18",
    )
    put_swpc(store, "202406200030three_day_forecast.txt", "3day_forecast", "issue 0030")
    return store


def trajectory(store: RawStore, window: Window = WINDOW) -> Trajectory:
    rec = store.records(source="nasa_oem")[0]
    eph = parse_oem(store.read(rec.id).decode("utf-8"))
    return from_oem(eph, provenance_of(rec), window, pad=timedelta(0))


def assess(store: RawStore, cutoff: datetime | None, window: Window = WINDOW) -> LineAssessment:
    return SepLine(load_sep_config()).assess(window, trajectory(store, window), AsOf(store, cutoff))


def test_cutoff_after_1230_issue_assesses_the_window_from_swpc_forecasts(tmp_path: Path) -> None:
    store = seed(tmp_path)
    replay = assess(store, CUTOFF_MAY_10_1230)
    review = assess(store, None)
    assert replay.availability == "available"
    assert replay.intervals and all(i.kind == "external_forecast" for i in replay.intervals)
    # D47: high ровно на минутах доступа (условная экспозиция без измеренного потока), остальное elevated.
    assert {i.level for i in replay.intervals} == {"elevated", "high"}
    high = [i for i in replay.intervals if i.level == "high"]
    assert _minutes(high) == replay.metrics["access_minutes"]
    assert all("high при реализации прогноза (D47)" in i.note for i in high)
    assert not any("доступ есть" in i.note for i in replay.intervals if i.level == "elevated")
    assert all("55 %" in i.rule and "3day_forecast" in i.rule and "D47" in i.rule for i in replay.intervals)
    assert any("условная экспозиция" in lim and "D47" in lim for lim in replay.limitations)
    # D62: при прогнозном Kp 8 широта обрезания 63.44° − 7.36° = 56.1°; пики всех семи витков окна 04:00–10:00
    # (65.9°, 61.4°, 64.5°, 60.9°, 61.6°, 58.1°, 58.0°) выше неё, поэтому пограничных витков в окне нет.
    assert not [lim for lim in replay.limitations if "пограничные витки" in lim]
    # D49: пограничный виток есть в окне 00:00–04:00: пик 55.7° в 02:20 не дотягивает до 56.1° при Kp 8 меньше чем
    # на единицу Kp (0.92°), а при Kp 9 (55.2°) открыт. Плечи открытых витков пограничными не считаются.
    early = assess(store, CUTOFF_MAY_10_1230, WINDOW_MAY_11_NIGHT)
    marginal = [lim for lim in early.limitations if "пограничные витки" in lim]
    assert len(marginal) == 1 and "02:" in marginal[0] and "D49" in marginal[0]
    assert "01:3" not in marginal[0] and "03:0" not in marginal[0] and "03:1" not in marginal[0]
    assert all("выпуск 2024-05-10 12:30" in i.note for i in replay.intervals)
    assert replay.intervals[0].start == WINDOW.start and replay.intervals[-1].end == WINDOW.end
    versions = {p.data_version for p in replay.intervals[0].provenance}
    assert "issue 1230" in versions and "reprocessed v3-0-2" not in versions
    assert any("персистентности" in lim for lim in replay.limitations)
    assert any("55 %" in lim and "порог 50 %" in lim for lim in replay.limitations)
    assert replay.confidence.grade == "low"
    assert 0.0 < replay.metrics["access_minutes"] <= review.metrics["access_minutes"]
    assert replay.metrics["coverage"] == 1.0 and replay.metrics["forecast_fraction"] == 1.0
    assert replay.metrics["persistence_minutes"] == 0.0 and "max_p10_pfu" not in replay.metrics


def test_cutoff_on_may_9_has_only_the_may_8_evening_forecast(tmp_path: Path) -> None:
    store = seed(tmp_path)
    early = assess(store, CUTOFF_MAY_9)
    later = assess(store, CUTOFF_MAY_10_1230)
    assert early.availability == "missing"
    assert early.intervals and {i.level for i in early.intervals} == {"unknown"}
    assert all(i.kind == "external_forecast" for i in early.intervals)
    assert all("geomag_forecast" in i.note and "выпуск 2024-05-08 22:05" in i.note for i in early.intervals)
    used = {(p.product, p.published_at) for p in early.intervals[0].provenance}
    assert ("geomag_forecast", datetime(2024, 5, 8, 22, 5, tzinfo=UTC)) in used
    assert not any(p.source == "goes_sgps" for p in early.intervals[0].provenance)
    assert any("нет прогноза вероятности протонного события на 2024-05-11" in lim for lim in early.limitations)
    assert any("нет данных о потоке протонов" in lim for lim in early.limitations)
    assert 0.0 <= early.metrics["access_minutes"] <= later.metrics["access_minutes"]
    assert early.metrics["coverage"] == 0.0 and early.confidence.grade == "low"


def test_persistence_covers_six_hours_after_last_sample_then_outlook(tmp_path: Path) -> None:
    store = seed(tmp_path)
    result = assess(store, CUTOFF_MAY_11_0200)
    assert result.availability == "available"
    assert result.metrics["persistence_minutes"] == 116.0 and result.metrics["coverage"] == 1.0
    persisted = [i for i in result.intervals if i.start < datetime(2024, 5, 11, 5, 56, tzinfo=UTC)]
    outlook = [i for i in result.intervals if i.start >= datetime(2024, 5, 11, 5, 56, tzinfo=UTC)]
    assert persisted and outlook
    assert all("персистентность отсчёта 2024-05-10 23:55" in i.note for i in persisted)
    assert all(i.kind == "external_forecast" and "geomag_forecast" in i.note for i in result.intervals)
    assert {i.level for i in persisted} <= {"elevated", "high", "critical"}
    # К отсечению 02:00 DONKI знает событие ≥10 MeV с 10 мая 13:35 (сообщение 20240510-AL-004): по D60 оно
    # считается идущим 48 ч после последнего подтверждения, то есть весь период, и задаёт уровень вместо
    # вероятности SWPC. События ≥100 MeV к 02:00 ещё нет (оно началось в 02:10), поэтому critical нет.
    assert {i.level for i in outlook} <= {"elevated", "high"}
    assert all("DONKI" in i.rule and "D60" in i.rule for i in outlook)
    assert all((i.level == "high") == ("D47" in i.note) for i in outlook)
    assert any("2024-05-10 23:55" in lim and "персистентность" in lim for lim in result.limitations)
    assert any("99 %" in lim and "daypre" in lim for lim in result.limitations)
    assert result.metrics["max_p10_pfu"] > 10.0
    assert result.confidence.grade == "low"


def test_review_mode_is_unchanged_by_replay_rules(tmp_path: Path) -> None:
    result = assess(seed(tmp_path), None)
    assert result.availability == "available" and result.confidence.grade == "medium"
    assert all(i.kind == "computed" and i.note == "" for i in result.intervals)
    assert result.metrics["forecast_fraction"] == 0.0 and result.metrics["persistence_minutes"] == 0.0
    assert {p.source for p in result.intervals[0].provenance} == {"goes_sgps", "gfz_kp", "nasa_oem"}
    assert not any("D39" in lim for lim in result.limitations)


def test_cutoff_inside_window_uses_known_kp_before_and_forecast_after(tmp_path: Path) -> None:
    store = seed(tmp_path)
    cutoff = datetime(2024, 5, 11, 7, 0, tzinfo=UTC)
    result = assess(store, cutoff)
    before = [i for i in result.intervals if i.start <= cutoff]
    after = [i for i in result.intervals if i.start > cutoff]
    assert before and after
    assert before[-1].end == cutoff + timedelta(minutes=1) and after[0].start == cutoff + timedelta(minutes=1)
    # D54: наблюдённого Kp за интервалы 03–06 и 06–09 UT к отсечению 07:00 ещё нет (выпуск 12:30 позже), поэтому
    # до отсечения берётся последний прогноз на интервал (выпуск 00:30: 7.00 и 8.33), а не спокойный порог.
    assert all(i.kind == "external_forecast" for i in before) and all(i.kind == "external_forecast" for i in after)
    assert all("взят последний прогноз на интервал" in i.note for i in before)
    assert not any("Kp недоступен" in lim for lim in result.limitations)
    # D44: точки 04:00–07:00 позже последнего отсчёта (23:55) покрыты состоянием по DONKI (оба события с 02:10);
    # при Kp 7.00 широта обрезания 62.6°, витки 04:42 (65.7°) и 06:16 (64.2°) открыты: critical по DONKI ≥100 MeV.
    assert result.availability == "available" and result.metrics["coverage"] == 1.0
    assert {i.level for i in before} == {"elevated", "critical"}
    assert all("DONKI: ≥100 MeV > 1 pfu с 2024-05-11 02:10 (выпуск 2024-05-11 02:30)" in i.note for i in before)
    assert all("DONKI: ≥10 MeV > 10 pfu с 2024-05-11 02:10 (выпуск 2024-05-11 02:46)" in i.note for i in before)
    assert result.metrics["donki_state_minutes"] >= 181.0 and result.metrics["persistence_minutes"] == 0.0


def test_next_day_beyond_horizon_takes_level_from_outlook_probability(tmp_path: Path) -> None:
    store = seed(tmp_path)
    result = assess(store, CUTOFF_MAY_10_1230, WINDOW_MAY_12)
    assert result.availability == "available"
    assert result.intervals and all(i.kind == "external_forecast" for i in result.intervals)
    assert {i.level for i in result.intervals} == {"elevated", "high"}
    assert all(
        "вероятности протонного события 55 % (3day_forecast" in i.rule and "D42" in i.rule for i in result.intervals
    )
    assert all("поток: внешний прогноз вероятности 55 %" in i.note for i in result.intervals)
    assert all((i.level == "high") == ("доступ есть, поток не измерен" in i.note) for i in result.intervals)
    assert all("выпуск 2024-05-10 12:30" in i.note for i in result.intervals)
    assert result.intervals[0].start == WINDOW_MAY_12.start and result.intervals[-1].end == WINDOW_MAY_12.end
    assert not any(p.source == "goes_sgps" for p in result.intervals[0].provenance)
    assert result.metrics["outlook_fraction"] == 1.0 and result.metrics["forecast_fraction"] == 1.0
    assert result.metrics["coverage"] == 1.0 and result.metrics["persistence_minutes"] == 0.0
    assert "max_p10_pfu" not in result.metrics
    assert result.confidence.grade == "low" and result.confidence.depends_on == CONFIDENCE_OUTLOOK_ONLY
    with_access = [i for i in result.intervals if "доступ есть, поток не измерен" in i.note]
    assert result.metrics["access_minutes"] > 0.0 and with_access
    assert sum((i.end - i.start).total_seconds() / 60.0 for i in with_access) == result.metrics["access_minutes"]
    assert any("D42" in lim for lim in result.limitations)
    assert any("на 2024-05-12: 55 %" in lim and "порог 50 %" in lim for lim in result.limitations)
    assert not any("нет прогноза вероятности" in lim for lim in result.limitations)


def test_quiet_outlook_below_threshold_gives_none_as_external_forecast(tmp_path: Path) -> None:
    store = seed_june(tmp_path)
    result = assess(store, CUTOFF_JUNE_20_0100, WINDOW_JUNE_21)
    assert result.availability == "available"
    assert result.intervals and {i.level for i in result.intervals} == {"none"}
    assert all(i.kind == "external_forecast" for i in result.intervals)
    assert all(
        "вероятности протонного события 5 % (3day_forecast" in i.rule and "D42" in i.rule for i in result.intervals
    )
    assert all("поток: внешний прогноз вероятности 5 %" in i.note for i in result.intervals)
    assert all("выпуск 2024-06-20 00:30" in i.note for i in result.intervals)
    assert result.intervals[0].start == WINDOW_JUNE_21.start and result.intervals[-1].end == WINDOW_JUNE_21.end
    assert {p.product for p in result.intervals[0].provenance} == {"3day_forecast", "oem"}
    assert result.metrics["outlook_fraction"] == 1.0 and result.metrics["forecast_fraction"] == 1.0
    assert result.metrics["coverage"] == 1.0
    # Спокойная широта обрезания 63.44° (D62) ниже пиков трассы: минуты доступа есть и в спокойные сутки, но при
    # вероятности 5 % уровень на них остаётся none. Их число лежит между счётом при Kp 0 и при Kp 4 (59.76°).
    tr = trajectory(store, WINDOW_JUNE_21)
    inside = [
        abs(m) for t, m in zip(tr.times, tr.mlat_deg, strict=True) if WINDOW_JUNE_21.start <= t < WINDOW_JUNE_21.end
    ]
    at_kp0 = float(sum(1 for m in inside if m >= 63.44))
    at_kp4 = float(sum(1 for m in inside if m >= 59.76))
    assert at_kp0 <= result.metrics["access_minutes"] <= at_kp4
    assert result.metrics["unmeasured_minutes"] == 360.0
    assert "max_p10_pfu" not in result.metrics
    assert result.confidence.grade == "low" and result.confidence.depends_on == CONFIDENCE_OUTLOOK_ONLY
    assert any("нет данных о потоке протонов" in lim for lim in result.limitations)
    assert any("на 2024-06-21: 5 %" in lim and "порог 50 %" in lim for lim in result.limitations)
    assert any("D42" in lim for lim in result.limitations)
    assert LIMIT_NO_DONKI in result.limitations and result.metrics["donki_state_minutes"] == 0.0


def _minutes(intervals: list[Interval]) -> float:
    return sum((i.end - i.start).total_seconds() / 60.0 for i in intervals)


def _level_at(result: LineAssessment, moment: datetime) -> str:
    for i in result.intervals:
        if i.start <= moment < i.end:
            return i.level
    return "unknown"


def test_donki_event_known_at_16_00_is_held_through_the_next_day(tmp_path: Path) -> None:
    # DONKI: ≥10 MeV > 10 pfu с 13:35, выпуск 13:46, подтверждение 14:12; по D60 событие считается идущим 48 ч,
    # до 12 мая 14:12, то есть всё окно 11 мая 04:00–10:00. Измеренного потока позже подтверждения к 16:00 нет.
    result = assess(seed(tmp_path), CUTOFF_MAY_10_1600)
    assert result.availability == "available" and WINDOW.end < DONKI_HOLD_END_MAY_10
    assert {i.level for i in result.intervals} == {"elevated", "high"}
    assert all((i.level == "high") == ("доступ есть" in i.note) for i in result.intervals)
    assert all(i.kind == "external_forecast" for i in result.intervals)
    assert all("DONKI: ≥10 MeV > 10 pfu с 2024-05-10 13:35" in i.note for i in result.intervals)
    assert all("D60" in i.rule for i in result.intervals)
    assert result.metrics["donki_state_minutes"] == 360.0 and result.metrics["outlook_fraction"] == 0.0
    assert LIMIT_DONKI_STATE in result.limitations
    assert any("с 2024-05-10 13:35 UTC" in lim and "принято идущим 48 ч" in lim for lim in result.limitations)
    assert not any("не известно" in lim for lim in result.limitations)
    # Прогноз прихода CME (выпуск 9 мая 20:03, приход 10 мая 23:46 ± 7 ч, Kp 6–8) поднимает Kp 03–06 UT с 7.00 до 8,
    # ячейка 06–09 UT (8.33) остаётся за SWPC.
    early = [i for i in result.intervals if i.end <= datetime(2024, 5, 11, 6, 0, tzinfo=UTC)]
    middle = [i for i in result.intervals if datetime(2024, 5, 11, 6, 0, tzinfo=UTC) <= i.start < WINDOW_0900]
    assert early and all("Kp: прогноз прихода CME" in i.note and "выше прогноза SWPC" in i.note for i in early)
    assert middle and all(i.note.startswith("Kp: прогноз Kp SWPC 3day_forecast") for i in middle)
    assert all("не ниже прогноза прихода CME" in i.note for i in middle)
    assert result.metrics["cme_forecast_minutes"] == 180.0 and LIMIT_CME_KP in result.limitations
    assert any(p.source == "donki" for p in result.intervals[0].provenance)


def test_donki_event_holds_elevated_through_the_evening_window(tmp_path: Path) -> None:
    store = seed(tmp_path)
    result = assess(store, CUTOFF_MAY_10_1600, WINDOW_MAY_10_EVENING)
    assert result.availability == "available" and {i.level for i in result.intervals} == {"elevated"}
    note = "DONKI: ≥10 MeV > 10 pfu с 2024-05-10 13:35 (выпуск 2024-05-10 13:46)"
    # Удержание 48 ч от подтверждения 14:12 покрывает всё окно 16:00–22:00.
    assert all(note in i.note for i in result.intervals)
    assert result.intervals[0].start == WINDOW_MAY_10_EVENING.start
    assert result.intervals[-1].end == WINDOW_MAY_10_EVENING.end
    assert result.metrics["donki_state_minutes"] == _minutes(list(result.intervals)) == 360.0
    # Точка 16:00 это момент отсечения: наблюдённого Kp за интервал 15–18 UT ещё нет, берётся последний прогноз
    # на интервал (D54); дальше Kp из прогноза.
    assert "взят последний прогноз на интервал" in result.intervals[0].note
    assert all(i.kind == "external_forecast" for i in result.intervals)
    assert all("D44" in i.rule and "D60" in i.rule for i in result.intervals)
    assert LIMIT_DONKI_STATE in result.limitations
    assert any(
        "≥10 MeV > 10 pfu с 2024-05-10 13:35 UTC (выпуск 2024-05-10 13:46, последнее подтверждение 2024-05-10 14:12)"
        in lim
        and "принято идущим 48 ч" in lim
        for lim in result.limitations
    )
    assert not any("не известно" in lim for lim in result.limitations)
    assert result.confidence.grade == "low" and result.confidence.depends_on == CONFIDENCE_DONKI
    assert "max_p10_pfu" not in result.metrics and result.metrics["persistence_minutes"] == 0.0
    assert any(p.source == "donki" for p in result.intervals[0].provenance)


def test_donki_100_mev_event_gives_critical_on_access_minutes(tmp_path: Path) -> None:
    store = seed(tmp_path)
    result = assess(store, CUTOFF_MAY_11_0300)
    review = assess(store, None)
    assert result.availability == "available" and result.metrics["access_minutes"] > 0.0
    critical = [i for i in result.intervals if i.level == "critical"]
    assert critical and all(i.kind == "external_forecast" for i in critical)
    # Событие ≥100 MeV > 1 pfu с 02:10 (выпуск 02:30, подтверждение 02:46) удерживается 24 ч, до 12 мая 02:46:
    # всё окно 04:00–10:00 внутри удержания; critical при доступе.
    assert WINDOW.end < DONKI_P100_HOLD_END
    assert all(WINDOW.start <= i.start and i.end <= WINDOW.end for i in critical)
    assert _minutes(critical) == result.metrics["access_minutes"] <= review.metrics["access_minutes"]
    assert all(_level_at(review, i.start) == "critical" for i in critical)
    assert all("DONKI: ≥100 MeV > 1 pfu с 2024-05-11 02:10 (выпуск 2024-05-11 02:30)" in i.note for i in critical)
    assert all("D44" in i.rule and "порог ESPE" in i.rule for i in critical)
    # Персистентный отсчёт 23:55 (p100 ниже порога) не мешает DONKI поднять уровень.
    assert critical[0].start < datetime(2024, 5, 11, 5, 56, tzinfo=UTC)
    assert "персистентность отсчёта 2024-05-10 23:55" in critical[0].note
    assert result.metrics["max_p100_pfu"] < 1.0 and result.metrics["persistence_minutes"] == 116.0
    # За горизонтом персистентности (после 05:55) уровень задаёт DONKI до конца окна, не вероятность SWPC.
    beyond = [i for i in result.intervals if i.start >= datetime(2024, 5, 11, 5, 56, tzinfo=UTC)]
    assert beyond and beyond[-1].end == WINDOW.end and all("DONKI:" in i.note for i in beyond)
    donki = [i for i in result.intervals if "DONKI:" in i.note]
    assert {i.level for i in result.intervals} == {"elevated", "critical"}
    assert result.metrics["donki_state_minutes"] == _minutes(donki)
    assert result.metrics["cme_forecast_minutes"] > 0.0
    assert LIMIT_DONKI_STATE in result.limitations
    assert any("≥100 MeV > 1 pfu с 2024-05-11 02:10 UTC (выпуск 2024-05-11 02:30" in lim for lim in result.limitations)
    assert any("≥10 MeV > 10 pfu с 2024-05-11 02:10 UTC (выпуск 2024-05-11 02:46" in lim for lim in result.limitations)
    assert any("принято идущим 24 ч" in lim for lim in result.limitations)
    assert not any("не известно" in lim for lim in result.limitations)
    assert result.confidence.grade == "low"
    assert {p.source for p in critical[0].provenance} >= {"donki", "goes_sgps", "nasa_oem", "swpc_archive"}


def test_donki_100_mev_hold_expires_after_24_hours_and_the_10_mev_event_is_still_held(tmp_path: Path) -> None:
    # Окно 12 мая 04:00–10:00 при отсечении 11 мая 03:00: удержание ≥100 MeV истекло 12 мая в 02:46, critical нет;
    # событие ≥10 MeV с 02:10 удерживается 48 ч, до 13 мая 02:46, поэтому уровень не ниже elevated.
    result = assess(seed(tmp_path), CUTOFF_MAY_11_0300, WINDOW_MAY_12)
    assert result.availability == "available" and WINDOW_MAY_12.start > DONKI_P100_HOLD_END
    assert {i.level for i in result.intervals} <= {"elevated", "high"}
    assert all("DONKI: ≥10 MeV > 10 pfu с 2024-05-11 02:10" in i.note for i in result.intervals)
    assert not any("≥100 MeV" in i.note for i in result.intervals)
    assert any(
        "≥100 MeV с 2024-05-11 02:10 UTC после 2024-05-12 02:46 UTC не известно" in lim for lim in result.limitations
    )


def test_missing_kp_never_reads_as_quiet_access_is_taken_at_kp_nine(tmp_path: Path) -> None:
    # Хранилище без единого продукта Kp: только поток GOES за 10 мая и эфемерида. При «спокойном» пороге 63.4°
    # открыты были бы только вершины самых высоких витков, и отказ источника Kp уменьшал бы число минут доступа,
    # то есть читался бы как более благоприятная обстановка (D54).
    store = RawStore(tmp_path)
    store.put(
        source="goes_sgps",
        product="sgps_l2_avg5m",
        url="https://x.invalid/g20240510.nc",
        content=Path("tests/fixtures/goes/sci_sgps-l2-avg5m_g18_d20240510_v3-0-2.nc").read_bytes(),
        fetched_at=F,
        published_at=datetime(2024, 5, 11, 0, 5, tzinfo=UTC),
        published_at_kind="derived",
        content_type="application/x-netcdf",
        data_version="reprocessed v3-0-2",
    )
    store.put(
        source="nasa_oem",
        product="oem",
        url="https://x.invalid/oem.txt",
        content=Path("tests/fixtures/nasa_oem/2024-05-12_ISS.OEM_J2K_EPH.txt").read_bytes(),
        fetched_at=F,
        published_at=datetime(2024, 5, 10, 18, 51, 53, tzinfo=UTC),
        published_at_kind="exact",
        content_type="text/plain",
        data_version="oem",
    )
    result = assess(store, CUTOFF_MAY_11_0200)
    assert result.metrics["access_minutes"] > 0.0
    assert any("Kp недоступен" in lim and "Kp 9" in lim and "D54" in lim for lim in result.limitations)
    assert all("доступ принят по Kp 9" in i.note for i in result.intervals)
    # Минуты доступа это минуты окна с |mlat| не ниже широты обрезания при Kp 9: 63.44° − 0.92° · 9 = 55.16° (D62).
    tr = trajectory(store)
    expected = sum(
        1
        for moment, mlat in zip(tr.times, tr.mlat_deg, strict=True)
        if WINDOW.start <= moment < WINDOW.end and abs(mlat) >= 55.16
    )
    assert result.metrics["access_minutes"] == float(expected) > 0.0


def test_unreadable_forecast_record_is_named_in_the_limitations(tmp_path: Path) -> None:
    # Обрезанный выпуск 3-day forecast рядом с настоящими не роняет линию; он назван в ограничениях.
    store = seed(tmp_path)
    store.put(
        source="swpc_archive",
        product="3day_forecast",
        url="https://x.invalid/202405100600three_day_forecast.txt",
        content=b":Product: 3-Day Forecast\n",
        fetched_at=F,
        published_at=datetime(2024, 5, 10, 6, 0, tzinfo=UTC),
        published_at_kind="exact",
        content_type="text/plain",
        data_version="issue 0600",
    )
    result = assess(store, CUTOFF_MAY_10_1230)
    assert result.availability == "available"
    named = [lim for lim in result.limitations if "записи источников не разобраны" in lim]
    assert len(named) == 1 and "swpc_archive/3day_forecast" in named[0]


def test_interval_provenance_names_exactly_the_donki_messages_that_set_its_level(tmp_path: Path) -> None:
    # D81: при отсечении 11 мая 03:00 уровень окна 04:00–10:00 задают события с 02:10 (сообщения 02:30 и 02:46);
    # сообщения 10 мая в провенанс не входят, а интервал, где уровень задан не DONKI, сообщений SEP не называет.
    donki = parse_notifications(Path("tests/fixtures/donki/notifications_2024-05-09_2024-05-12.json").read_bytes())
    sep_messages = {f"сообщение {n.message_id}" for n in donki if n.message_type == "SEP"}
    result = assess(seed(tmp_path), CUTOFF_MAY_11_0300)
    for i in result.intervals:
        used = {p.data_version for p in i.provenance if p.data_version in sep_messages}
        if "DONKI:" in i.note:
            assert used == {"сообщение 20240511-AL-006", "сообщение 20240511-AL-007"}
        else:
            assert used == set()
    assert any("DONKI:" not in i.note for i in result.intervals)
