from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from evarisk.orbit.oem import parse_oem
from evarisk.orbit.trajectory import Trajectory, from_oem
from evarisk.risk.lines.meteors import MeteorLine, load_meteor_config
from evarisk.store.asof import AsOf
from evarisk.store.raw import RawStore
from evarisk.store.views import provenance_of
from evarisk.types import LineAssessment, Window

F = datetime(2026, 9, 18, tzinfo=UTC)
OEM = Path("tests/fixtures/nasa_oem/2024-05-12_ISS.OEM_J2K_EPH.txt")
FLUX = Path("tests/fixtures/meo/flux_data_2024_excerpt.txt")
# Запись NTRS 20230015158 распространена 2 ноября 2023 года; доступность это конец этих суток.
MEO_2024_AVAILABLE = datetime(2023, 11, 2, 23, 59, tzinfo=UTC)


def seed_oem(store: RawStore) -> None:
    store.put(
        source="nasa_oem",
        product="oem",
        url="https://x.invalid/oem.txt",
        content=OEM.read_bytes(),
        fetched_at=F,
        published_at=datetime(2024, 5, 10, 18, 51, 53, tzinfo=UTC),
        published_at_kind="exact",
        content_type="text/plain",
        data_version="oem 2024-05-10",
    )


def seed(tmp_path: Path) -> RawStore:
    store = RawStore(tmp_path)
    store.put(
        source="meteor_docs",
        product="meo_flux_2024",
        url="https://ntrs.nasa.gov/api/citations/20230015158/downloads/flux_data.txt",
        content=FLUX.read_bytes(),
        fetched_at=F,
        published_at=MEO_2024_AVAILABLE,
        published_at_kind="derived",
        content_type="text/plain",
        data_version="MEO 2024, NTRS 20230015158, distributed 2023-11-02",
    )
    seed_oem(store)
    return store


def assess(store: RawStore, start: datetime, cutoff: datetime | None = None) -> LineAssessment:
    window = Window(start=start, end=start + timedelta(hours=6))
    rec = store.records(source="nasa_oem")[0]
    tr = from_oem(parse_oem(store.read(rec.id).decode("utf-8")), provenance_of(rec), window, pad=timedelta(0))
    return MeteorLine(load_meteor_config()).assess(window, tr, AsOf(store, cutoff))


def test_eta_aquariids_tail_is_below_the_threshold_for_suit_penetrating_particles(tmp_path: Path) -> None:
    result = assess(seed(tmp_path), datetime(2024, 5, 11, 4, 0, tzinfo=UTC))
    assert result.availability == "available" and result.line == "meteor_showers"
    assert result.mechanism == "meteoroids"
    assert result.intervals and all(i.kind == "external_forecast" for i in result.intervals)
    assert all(any(p.product == "meo_flux_2024" for p in i.provenance) for i in result.intervals)
    # Файл MEO 2024: 11 мая 04:00–10:00 коэффициент для 6.7 Дж 0.049–0.051 (спад эта-Акварид), ниже порога 0.2.
    assert {i.level for i in result.intervals} == {"none"}
    assert 0.0 <= result.metrics["max_factor"] <= result.metrics["max_factor_unshielded"] < 0.06
    # 11 мая активны только эта-Аквариды с радиантом у небесного экватора (RA 338°, Dec −1°): на части каждого
    # витка радиант за Землёй, эти минуты экранированы.
    assert 0 < result.metrics["shielded_minutes"] < 360
    assert result.metrics["suit_energy_joule"] == 6.7
    assert result.confidence.grade == "medium"
    assert any("пробивающих скафандр" in lim for lim in result.limitations)


def test_intervals_tile_the_window(tmp_path: Path) -> None:
    start = datetime(2024, 5, 11, 4, 0, tzinfo=UTC)
    result = assess(seed(tmp_path), start)
    assert result.intervals[0].start == start and result.intervals[-1].end == start + timedelta(hours=6)
    for a, b in zip(result.intervals, result.intervals[1:], strict=False):
        assert a.end == b.start and a.level != b.level


def _still_trajectory(start: datetime, minutes: int, r_km: np.ndarray, like: Trajectory) -> Trajectory:
    times = [start + timedelta(minutes=i) for i in range(minutes)]
    n = len(times)
    zeros = np.zeros(n)
    return Trajectory(
        provider=like.provider,
        provenance=like.provenance,
        epoch=like.epoch,
        is_reconstruction=True,
        times=times,
        r_km=np.repeat(r_km.reshape(1, 3), n, axis=0),
        lat_deg=zeros,
        lon_deg=zeros,
        alt_km=zeros + 400.0,
        sunlit=np.ones(n, dtype=bool),
        mlat_deg=zeros,
        l_shell=zeros + 1.0,
        b_total_nt=zeros,
        in_saa=np.zeros(n, dtype=bool),
        limitations=(),
    )


def test_daytime_arietids_give_elevated_for_the_whole_hour(tmp_path: Path) -> None:
    store = seed(tmp_path)
    rec = store.records(source="nasa_oem")[0]
    like = from_oem(
        parse_oem(store.read(rec.id).decode("utf-8")),
        provenance_of(rec),
        Window(start=datetime(2024, 5, 11, 4, tzinfo=UTC), end=datetime(2024, 5, 11, 5, tzinfo=UTC)),
        pad=timedelta(0),
    )
    start = datetime(2024, 6, 8, 6, tzinfo=UTC)
    tr = _still_trajectory(start, 60, np.asarray([6771.0, 0.0, 0.0]), like)
    result = MeteorLine(load_meteor_config()).assess(
        Window(start=start, end=start + timedelta(hours=1)), tr, AsOf(store, None)
    )
    # Файл MEO 2024: 8 июня 06:00 коэффициент 0.2948, 07:00 0.2957; активны Дневные Ариетиды и потоки без радианта
    # в прогнозе MEO (Дневные дзета-Персеиды, Южные мю-Стрельциды), поэтому экранирования нет и весь час elevated.
    assert [i.level for i in result.intervals] == ["elevated"]
    assert result.metrics["shielded_minutes"] == 0.0
    assert 0.294 < result.metrics["max_factor"] < 0.296


def test_missing_forecast_is_missing(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    seed_oem(store)
    result = assess(store, datetime(2024, 5, 11, 4, 0, tzinfo=UTC))
    assert result.availability == "missing" and result.intervals == ()
    assert result.confidence.grade == "low"
    assert any("машиночитаемого прогноза NASA MEO на 2024" in lim for lim in result.limitations)


def test_replay_cutoff_before_forecast_takes_the_line_out_of_the_comparison(tmp_path: Path) -> None:
    # Прогноз MEO на 2024 год распространён 2 ноября 2023: при отсечении 1 октября 2023 его ещё нет. Линия не
    # считается и помечается как недоступная на момент отсечения, в сравнении окон она не участвует (D65).
    store = seed(tmp_path)
    result = assess(store, datetime(2024, 5, 11, 4, 0, tzinfo=UTC), cutoff=datetime(2023, 10, 1, tzinfo=UTC))
    assert result.availability == "unusable_for_replay" and result.intervals == ()
    assert any("позже отсечения" in lim and "2023-11-02" in lim and "D65" in lim for lim in result.limitations)
    later = assess(store, datetime(2024, 5, 11, 4, 0, tzinfo=UTC), cutoff=datetime(2024, 5, 11, 3, 0, tzinfo=UTC))
    assert later.availability == "available"
