import json
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np

from evarisk.parsers.swpc_text import issued_at
from evarisk.store.asof import AsOf
from evarisk.store.raw import RawStore
from evarisk.store.views import LIVE_PROTON_NOTE, LIVE_PROTON_VERSION, PROTON_NOTE, proton_series
from evarisk.store.views_kp import (
    KP_1M_VERSION,
    LIVE_OBSERVED_VERSION,
    NOTE_ELAPSED_CELL,
    NOTE_KP_1M_MAX,
    NOTE_LIVE_OBSERVED,
    kp_as_known,
    kp_forecast,
    proton_event_probability,
)

FIX = Path("tests/fixtures/swpc_live")
GOES = "tests/fixtures/goes/sci_sgps-l2-avg5m_g18_d{day}_v3-0-2.nc"
# Снимки живых продуктов в фикстурах сделаны 18 сентября 2026 около 17:19 UTC: последний отсчёт протонов 17:15,
# последняя минута Kp 17:18, выпуск 3-day forecast 12:30.
SNAPSHOT = datetime(2026, 9, 18, 17, 20, tzinfo=UTC)
LIVE_FILES = {
    "protons_7d": "integral-protons-7-day.json",
    "kp_forecast": "noaa-planetary-k-index-forecast.json",
    "kp_1m": "planetary_k_index_1m.json",
}


def put_live_json(store: RawStore, product: str, content: bytes, fetched_at: datetime, url: str = "") -> None:
    store.put(
        source="swpc_live",
        product=product,
        url=url or f"https://x.invalid/{product}.json",
        content=content,
        fetched_at=fetched_at,
        published_at=fetched_at,
        published_at_kind="derived",
        content_type="application/json",
        data_version="live",
        notes="снимок; время каждого отсчёта в time_tag",
    )


def put_live_three_day(store: RawStore) -> None:
    text = (FIX / "3-day-forecast.txt").read_text(encoding="utf-8")
    store.put(
        source="swpc_live",
        product="3day_forecast",
        url="https://x.invalid/3-day-forecast.txt",
        content=text.encode("utf-8"),
        fetched_at=SNAPSHOT,
        published_at=issued_at(text),
        published_at_kind="exact",
        content_type="text/plain",
        data_version="issue 1230",
    )


def seed(tmp_path: Path) -> RawStore:
    store = RawStore(tmp_path)
    for product, name in LIVE_FILES.items():
        put_live_json(store, product, (FIX / name).read_bytes(), SNAPSHOT)
    put_live_three_day(store)
    return store


def proton_rows(samples: list[tuple[str, dict[float, float]]]) -> bytes:
    rows = [
        {"time_tag": stamp, "satellite": 18, "flux": flux, "energy": f">={energy:g} MeV"}
        for stamp, fluxes in samples
        for energy, flux in fluxes.items()
    ]
    return json.dumps(rows).encode()


def t(hour: int, minute: int = 0, day: int = 18) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=UTC)


def test_proton_series_from_live_integral_fluxes(tmp_path: Path) -> None:
    s = proton_series(AsOf(seed(tmp_path), SNAPSHOT), t(0), t(0, day=19))
    assert s is not None and s.kind == "observation" and s.data_version == LIVE_PROTON_VERSION
    assert s.note == LIVE_PROTON_NOTE
    # 18 сентября в фикстуре без пропусков: отсчёты каждые 5 минут с 00:00 по 17:15.
    assert len(s.times) == 208 and s.times[0] == t(0) and s.times[-1] == t(17, 15)
    assert s.p10.shape == s.p30.shape == s.p50.shape == s.p100.shape == (208,)
    assert not np.isnan(s.p10).any() and not np.isnan(s.p30).any()
    assert not np.isnan(s.p50).any() and not np.isnan(s.p100).any()
    # Последняя строка фикстуры (17:15): >=10 MeV 0.2168731540441513, >=30 MeV 0.2048204094171524,
    # >=50 MeV 0.2016373723745346, >=100 MeV 0.19406813383102417 pfu.
    assert float(s.p10[-1]) == 0.2168731540441513 and float(s.p30[-1]) == 0.2048204094171524
    assert float(s.p50[-1]) == 0.2016373723745346 and float(s.p100[-1]) == 0.19406813383102417
    assert float(s.p10[0]) == 0.22649641335010529
    # Интегральные потоки убывают с порогом.
    assert np.all(s.p100 <= s.p50) and np.all(s.p50 <= s.p30) and np.all(s.p30 <= s.p10)
    assert s.energies_mev.shape == (0,) and s.diff_flux.shape == (208, 0)
    assert [p.product for p in s.provenance] == ["protons_7d"] and s.provenance[0].source == "swpc_live"


def test_proton_series_respects_cutoff_before_fetch(tmp_path: Path) -> None:
    store = seed(tmp_path)
    assert proton_series(AsOf(store, t(12)), t(0), t(0, day=19)) is None
    assert proton_series(AsOf(store, None), t(0, day=19), t(0, day=20)) is None


def test_later_live_record_overrides_earlier_and_adds_tail(tmp_path: Path) -> None:
    store = seed(tmp_path)
    later = proton_rows(
        [
            ("2026-09-18T17:15:00Z", {10: 0.5, 30: 0.4, 50: 0.3, 100: 0.2}),
            ("2026-09-18T17:20:00Z", {10: 0.6, 30: 0.4, 50: 0.3, 100: 0.2}),
        ]
    )
    put_live_json(store, "protons_7d", later, t(17, 25), url="https://x.invalid/later.json")
    s = proton_series(AsOf(store, t(17, 30)), t(17), t(18))
    assert s is not None and s.times == [t(17), t(17, 5), t(17, 10), t(17, 15), t(17, 20)]
    assert float(s.p10[3]) == 0.5 and float(s.p10[4]) == 0.6 and float(s.p100[4]) == 0.2
    assert [p.url for p in s.provenance] == ["https://x.invalid/protons_7d.json", "https://x.invalid/later.json"]
    only_earlier = proton_series(AsOf(store, t(17, 22)), t(17), t(18))
    assert only_earlier is not None and float(only_earlier.p10[3]) == 0.2168731540441513


def test_live_sample_without_some_energies_gives_nan_not_zero(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    put_live_json(store, "protons_7d", proton_rows([("2026-09-18T17:15:00Z", {10: 0.5, 100: 0.2})]), SNAPSHOT)
    s = proton_series(AsOf(store, SNAPSHOT), t(17), t(18))
    assert s is not None and float(s.p10[0]) == 0.5 and float(s.p100[0]) == 0.2
    assert np.isnan(s.p30[0]) and np.isnan(s.p50[0])


def test_goes_days_take_priority_and_live_fills_the_tail(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    store.put(
        source="goes_sgps",
        product="sgps_l2_avg5m",
        url="https://x.invalid/20240511.nc",
        content=Path(GOES.format(day="20240511")).read_bytes(),
        fetched_at=SNAPSHOT,
        published_at=datetime(2024, 5, 12, 0, 5, tzinfo=UTC),
        published_at_kind="derived",
        content_type="application/x-netcdf",
        data_version="reprocessed v3-0-2",
    )
    live = proton_rows(
        [
            ("2024-05-11T23:55:00Z", {10: 999.0, 30: 999.0, 50: 999.0, 100: 999.0}),
            ("2024-05-12T00:00:00Z", {10: 3.0, 30: 2.0, 50: 1.5, 100: 1.0}),
            ("2024-05-12T00:05:00Z", {10: 3.1, 30: 2.1, 50: 1.6, 100: 1.1}),
        ]
    )
    put_live_json(store, "protons_7d", live, datetime(2024, 5, 12, 0, 10, tzinfo=UTC))
    start, end = datetime(2024, 5, 11, 22, tzinfo=UTC), datetime(2024, 5, 12, 1, tzinfo=UTC)
    s = proton_series(AsOf(store, None), start, end)
    assert s is not None and len(s.times) == 26 and s.times == sorted(s.times)
    assert s.times[23] == datetime(2024, 5, 11, 23, 55, tzinfo=UTC) and s.times[-1] == datetime(
        2024, 5, 12, 0, 5, tzinfo=UTC
    )
    # Отсчёт 23:55 из GOES, а не 999 из живого продукта; хвост 12 мая из живого продукта.
    assert float(s.p10[23]) < 999.0 and float(s.p10[24]) == 3.0 and float(s.p100[25]) == 1.1
    assert s.diff_flux.shape == (26, 13) and s.energies_mev.shape == (13,)
    assert not np.isnan(s.diff_flux[:24]).all() and np.isnan(s.diff_flux[24:]).all()
    assert s.kind == "computed" and s.data_version == f"reprocessed v3-0-2, {LIVE_PROTON_VERSION}"
    assert PROTON_NOTE in s.note and LIVE_PROTON_NOTE in s.note and "2024-05-11" in s.note
    assert [p.product for p in s.provenance] == ["sgps_l2_avg5m", "protons_7d"]
    goes_only = proton_series(AsOf(store, None), start, datetime(2024, 5, 12, tzinfo=UTC))
    assert goes_only is not None and goes_only.kind == "computed" and goes_only.note == PROTON_NOTE


def test_kp_forecast_from_live_json_gives_estimated_and_predicted(tmp_path: Path) -> None:
    store = seed(tmp_path)
    s = kp_forecast(AsOf(store, SNAPSHOT), t(17, 20), t(6, day=19))
    assert s is not None and s.kind == "external_forecast" and s.data_version == "snapshot 2026-09-18 17:20"
    # Строки снимка: 15:00 estimated 2.00, 18:00 estimated 2.67, 21:00 estimated 3.00,
    # 19 сентября 00:00 predicted 1.67, 03:00 2.00, 06:00 1.67.
    assert s.starts == [t(15), t(18), t(21), t(0, day=19), t(3, day=19), t(6, day=19)]
    assert s.kp.tolist() == [2.0, 2.67, 3.0, 1.67, 2.0, 1.67]
    assert [n.split("(")[1].split(")")[0] for n in s.notes] == ["estimated"] * 3 + ["predicted"] * 3
    assert [p.product for p in s.provenance] == ["kp_forecast"] and s.provenance[0].source == "swpc_live"


def test_kp_forecast_fills_a_day_missing_in_the_latest_snapshot_from_the_older_issue(tmp_path: Path) -> None:
    # Снимок kp_forecast около полуночи UTC без строк 19 сентября: самый свежий выпуск, но пустой на нужные сутки.
    rows = json.loads((FIX / LIVE_FILES["kp_forecast"]).read_text(encoding="utf-8"))
    without_day = json.dumps([r for r in rows if not r["time_tag"].startswith("2026-09-19")]).encode()
    store = RawStore(tmp_path)
    put_live_three_day(store)
    put_live_json(store, "kp_forecast", without_day, t(0, 5, day=19))
    s = kp_forecast(AsOf(store, t(0, 35, day=19)), t(1, day=19), t(13, day=19))
    assert s is not None and s.kind == "external_forecast"
    # Интервалы 19 сентября заполнены выпуском 3-day forecast 12:30 18 сентября:
    # 00-03UT 2.00, 03-06UT 2.33, 06-09UT 2.00; интервал 21:00 18 сентября истёк к 00:35 и в прогноз не входит.
    by_start = dict(zip(s.starts, s.kp.tolist(), strict=True))
    assert s.starts[0] == t(0, day=19)
    assert by_start[t(0, day=19)] == 2.0 and by_start[t(3, day=19)] == 2.33 and by_start[t(6, day=19)] == 2.0
    assert {n.split(",")[0] for n in s.notes} == {"прогноз Kp SWPC 3day_forecast"}
    assert {p.product for p in s.provenance} == {"3day_forecast"} and "issue 2026-09-18 12:30" in s.data_version
    # При более свежем полном снимке 17:20 его строки predicted для 19 сентября стоят выше текстового выпуска.
    full = seed(tmp_path / "full")
    put_live_json(full, "kp_forecast", without_day, t(0, 5, day=19), url="https://x.invalid/kp2.json")
    s2 = kp_forecast(AsOf(full, t(0, 35, day=19)), t(1, day=19), t(13, day=19))
    assert s2 is not None and dict(zip(s2.starts, s2.kp.tolist(), strict=True))[t(0, day=19)] == 1.67
    assert all("kp_forecast (predicted), снимок 2026-09-18 17:20" in n for n in s2.notes[:3])


def test_kp_forecast_from_live_three_day_before_json_snapshot(tmp_path: Path) -> None:
    s = kp_forecast(AsOf(seed(tmp_path), t(13)), t(17, 20), t(6, day=19))
    assert s is not None and s.data_version == "issue 2026-09-18 12:30"
    # Таблица выпуска 12:30: Sep 18 15-18UT 2.00, 18-21UT 2.67, 21-00UT 3.00;
    # Sep 19 00-03UT 2.00, 03-06UT 2.33, 06-09UT 2.00.
    assert s.kp.tolist() == [2.0, 2.67, 3.0, 2.0, 2.33, 2.0]
    assert s.provenance[0].source == "swpc_live" and s.provenance[0].product == "3day_forecast"
    assert kp_forecast(AsOf(seed(tmp_path), t(12)), t(17, 20), t(6, day=19)) is None


def test_kp_as_known_from_elapsed_cells_observed_rows_and_minute_maximum(tmp_path: Path) -> None:
    s = kp_as_known(AsOf(seed(tmp_path), SNAPSHOT), t(0), t(17, 20))
    assert s is not None and s.kind == "observation"
    got = dict(zip(s.starts, zip(s.kp.tolist(), s.notes, strict=True), strict=True))
    assert got == {
        t(21, day=17): (2.33, NOTE_LIVE_OBSERVED),
        t(0): (1.67, NOTE_ELAPSED_CELL),
        t(3): (2.0, NOTE_ELAPSED_CELL),
        t(6): (2.0, NOTE_ELAPSED_CELL),
        t(9): (1.67, NOTE_ELAPSED_CELL),
        t(12): (0.67, NOTE_LIVE_OBSERVED),
        t(15): (0.67, NOTE_KP_1M_MAX),
    }
    assert s.data_version == f"{KP_1M_VERSION}, swpc dated products, {LIVE_OBSERVED_VERSION}"
    assert {p.product for p in s.provenance} == {"3day_forecast", "kp_forecast", "kp_1m"}


def test_kp_as_known_minute_maximum_only_for_started_intervals(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    put_live_json(store, "kp_1m", (FIX / "planetary_k_index_1m.json").read_bytes(), SNAPSHOT)
    s = kp_as_known(AsOf(store, SNAPSHOT), t(12), t(23))
    assert s is not None and s.data_version == KP_1M_VERSION
    # Минуты фикстуры 11:21–17:18: интервал 09:00 без минут 09:00–11:20, максимум 1.67; 12:00 максимум 1.00;
    # 15:00 максимум 0.67; интервалы после 17:18 отсутствуют.
    assert dict(zip(s.starts, s.kp.tolist(), strict=True)) == {t(9): 1.67, t(12): 1.0, t(15): 0.67}
    assert all(n == NOTE_KP_1M_MAX for n in s.notes)
    assert kp_as_known(AsOf(store, t(11)), t(12), t(23)) is None


def test_proton_event_probability_from_live_three_day(tmp_path: Path) -> None:
    found = proton_event_probability(AsOf(seed(tmp_path), SNAPSHOT), date(2026, 9, 19))
    assert found is not None and found[0] == 1 and found[1].source == "swpc_live"
    assert proton_event_probability(AsOf(seed(tmp_path), SNAPSHOT), date(2026, 9, 21)) is None
