import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

from evarisk.orbit.oem import parse_oem
from evarisk.orbit.trajectory import Trajectory, from_oem
from evarisk.parsers.swpc_text import issued_at
from evarisk.risk.config import load_sep_config
from evarisk.risk.lines.dose_proxy import LIMIT_NO_SPECTRUM, DoseProxyLine, load_dose_proxy_config
from evarisk.risk.lines.sep import LIMIT_KP_FORECAST, LIMIT_NO_KP, SepLine
from evarisk.store.asof import AsOf
from evarisk.store.raw import RawStore
from evarisk.store.views import provenance_of
from evarisk.types import Window

F = datetime(2026, 9, 18, tzinfo=UTC)
WINDOW = Window(start=datetime(2024, 5, 11, 4, 0, tzinfo=UTC), end=datetime(2024, 5, 11, 10, 0, tzinfo=UTC))
# Период с отсечением внутри: до 03:00 Kp известный к отсечению или последний прогноз (D54), после прогноз (D39).
NIGHT = Window(start=datetime(2024, 5, 11, 0, 0, tzinfo=UTC), end=datetime(2024, 5, 11, 6, 0, tzinfo=UTC))
CUTOFF = datetime(2024, 5, 11, 3, 0, tzinfo=UTC)
PROXY_NOTE = "прокси дозы среды за номинальной защитой скафандра 0.18 г/см², не доза человека (D34)"
DOSE_METRICS = ("dose_proxy_usv_window", "dose_rate_max_usv_h")
SWPC = Path("tests/fixtures/swpc_archive")


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
    return store


def seed_kp_forecasts(store: RawStore) -> RawStore:
    """Прогнозы SWPC, выпущенные до отсечения: 3-day forecast 10 мая 00:30 и 12:30, geomag forecast 8–10 мая."""
    for name, product in (
        ("202405100030three_day_forecast.txt", "3day_forecast"),
        ("202405101230three_day_forecast.txt", "3day_forecast"),
        ("20240508geomag_forecast.txt", "geomag_forecast"),
        ("20240509geomag_forecast.txt", "geomag_forecast"),
        ("20240510geomag_forecast.txt", "geomag_forecast"),
    ):
        text = (SWPC / name).read_text(encoding="utf-8")
        store.put(
            source="swpc_archive",
            product=product,
            url=f"https://x.invalid/{name}",
            content=text.encode("utf-8"),
            fetched_at=F,
            published_at=issued_at(text),
            published_at_kind="exact",
            content_type="text/plain",
            data_version=name[:12],
        )
    return store


def trajectory(store: RawStore, window: Window = WINDOW) -> Trajectory:
    rec = store.records(source="nasa_oem")[0]
    eph = parse_oem(store.read(rec.id).decode("utf-8"))
    return from_oem(eph, provenance_of(rec), window, pad=timedelta(0))


def test_storm_window_dose_is_positive_and_below_a_millisievert(tmp_path: Path) -> None:
    store = seed(tmp_path)
    cfg = load_sep_config()
    result = DoseProxyLine(load_dose_proxy_config(), cfg).assess(WINDOW, trajectory(store), AsOf(store, None))
    assert result.line == "dose_proxy" and result.availability == "available"
    # Событие S2 (p10 около 100 pfu) при десятках минут доступа: десятки–сотни мкЗв; краткосрочный лимит NASA для
    # кожи 1500 мЗв/год, за шестичасовое окно ожидается меньше 1000 мкЗв. Порог по единицам: pSv→мкЗв 1e-6, 4π.
    dose = result.metrics["dose_proxy_usv_window"]
    assert 0.1 < dose < 1000.0, dose
    assert result.metrics["dose_rate_max_usv_h"] > 0.0 and 10.0 < result.metrics["shield_threshold_mev"] < 15.0
    assert result.metrics["access_minutes"] > 0.0
    assert result.confidence.grade == "low" and PROXY_NOTE in result.limitations
    assert all(i.kind == "computed" and i.provenance and i.rule for i in result.intervals)
    assert {i.level for i in result.intervals} == {"none", "elevated"}
    elevated_minutes = sum((i.end - i.start).total_seconds() / 60.0 for i in result.intervals if i.level == "elevated")
    assert elevated_minutes == result.metrics["access_minutes"]


def test_access_minutes_match_sep_line(tmp_path: Path) -> None:
    store = seed(tmp_path)
    cfg = load_sep_config()
    traj = trajectory(store)
    proxy = DoseProxyLine(load_dose_proxy_config(), cfg).assess(WINDOW, traj, AsOf(store, None))
    sep = SepLine(cfg).assess(WINDOW, traj, AsOf(store, None))
    assert proxy.metrics["access_minutes"] == sep.metrics["access_minutes"] > 0.0


def test_quiet_model_gives_less_dose_than_storm_model(tmp_path: Path) -> None:
    store = seed(tmp_path)
    cfg = load_sep_config()
    bare = cfg.model_copy(update={"kp_suppression_deg_per_kp": None, "dst_suppression_deg_per_nt": None})
    traj = trajectory(store)
    storm = DoseProxyLine(load_dose_proxy_config(), cfg).assess(WINDOW, traj, AsOf(store, None))
    quiet = DoseProxyLine(load_dose_proxy_config(), bare).assess(WINDOW, traj, AsOf(store, None))
    assert quiet.metrics["dose_proxy_usv_window"] < storm.metrics["dose_proxy_usv_window"]
    assert quiet.metrics["access_minutes"] < storm.metrics["access_minutes"]
    assert PROXY_NOTE in quiet.limitations


def test_replay_before_availability_is_missing_not_zero_dose(tmp_path: Path) -> None:
    store = seed(tmp_path)
    cutoff = datetime(2024, 5, 11, 3, 0, tzinfo=UTC)
    result = DoseProxyLine(load_dose_proxy_config(), load_sep_config()).assess(
        WINDOW, trajectory(store), AsOf(store, cutoff)
    )
    # Ряд за 10 мая уже доступен (выпуск 11 мая 00:05), но окно 11 мая он не покрывает: отсчётов нет, не «ноль».
    assert result.availability == "missing"
    assert "dose_proxy_usv_window" not in result.metrics and "dose_rate_max_usv_h" not in result.metrics
    assert result.intervals and all(i.level == "unknown" for i in result.intervals)
    assert PROXY_NOTE in result.limitations


def test_live_integral_fluxes_without_spectrum_give_missing(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
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
    stamps = [WINDOW.start + timedelta(minutes=5 * k) for k in range(72)]
    rows = [
        {"time_tag": f"{s:%Y-%m-%dT%H:%M:%SZ}", "satellite": 18, "flux": flux, "energy": energy}
        for s in stamps
        for energy, flux in ((">=10 MeV", 100.0), (">=30 MeV", 30.0), (">=50 MeV", 10.0), (">=100 MeV", 5.0))
    ]
    fetched = WINDOW.end + timedelta(minutes=10)
    store.put(
        source="swpc_live",
        product="protons_7d",
        url="https://x.invalid/protons_7d.json",
        content=json.dumps(rows).encode(),
        fetched_at=fetched,
        published_at=fetched,
        published_at_kind="derived",
        content_type="application/json",
        data_version="live",
    )
    result = DoseProxyLine(load_dose_proxy_config(), load_sep_config()).assess(
        WINDOW, trajectory(store), AsOf(store, fetched)
    )
    # Интегральные потоки S2 есть, спектра нет: прокси дозы не считается и не подменяется нулём.
    assert result.availability == "missing" and result.intervals == ()
    assert LIMIT_NO_SPECTRUM in result.limitations and PROXY_NOTE in result.limitations
    assert "dose_proxy_usv_window" not in result.metrics and "dose_rate_max_usv_h" not in result.metrics
    assert result.confidence.grade == "low"


def test_replay_access_minutes_match_sep_line_with_swpc_kp(tmp_path: Path) -> None:
    store = seed_kp_forecasts(seed(tmp_path))
    cfg = load_sep_config()
    traj = trajectory(store, NIGHT)
    proxy = DoseProxyLine(load_dose_proxy_config(), cfg).assess(NIGHT, traj, AsOf(store, CUTOFF))
    sep = SepLine(cfg).assess(NIGHT, traj, AsOf(store, CUTOFF))
    # D40: доза считается на минутах доступа линии SEP; в replay Kp берётся так же, как у неё (D39, D54).
    assert proxy.metrics["access_minutes"] == sep.metrics["access_minutes"] > 0.0
    # Definitive GFZ выходит в июле 2024 и к отсечению недоступен, наблюдённого Kp за 11 мая 00–06 UTC в прогнозах
    # SWPC к 03:00 нет: весь период опирается на прогноз Kp, это внешний прогноз, а не наш расчёт.
    assert proxy.intervals and all(i.kind == "external_forecast" for i in proxy.intervals)
    assert any(lim.startswith(LIMIT_KP_FORECAST.split("{", 1)[0]) for lim in proxy.limitations)
    assert any("последний прогноз на интервал" in i.note for i in proxy.intervals if i.start < CUTOFF)
    # D62: спокойная широта обрезания для 30 MeV 63.44°; прогнозная буря открывает витки с пиком ниже неё, поэтому
    # доступ по Kp SWPC шире доступа спокойной модели, которую давал пропуск definitive Kp.
    r30 = math.sqrt(30.0**2 + 2.0 * 30.0 * cfg.proton_rest_energy_mev) / 1000.0
    quiet = math.degrees(math.acos(((r30 + cfg.cutoff_offset_gv) / cfg.cutoff_constant_gv) ** 0.25))
    assert 63.4 < quiet < 63.5
    in_window = [i for i, t in enumerate(traj.times) if NIGHT.start <= t < NIGHT.end]
    quiet_minutes = sum(1 for i in in_window if abs(float(traj.mlat_deg[i])) >= quiet)
    assert proxy.metrics["access_minutes"] > quiet_minutes


def test_replay_without_kp_assumes_kp_9_like_sep_line(tmp_path: Path) -> None:
    store = seed(tmp_path)
    cfg = load_sep_config()
    traj = trajectory(store)
    proxy = DoseProxyLine(load_dose_proxy_config(), cfg).assess(WINDOW, traj, AsOf(store, CUTOFF))
    sep = SepLine(cfg).assess(WINDOW, traj, AsOf(store, CUTOFF))
    # К отсечению нет ни definitive GFZ (июль 2024), ни продуктов SWPC: Kp неизвестен, доступ по Kp 9 (D54).
    # Широта обрезания 30 MeV при Kp 9 по фиту SAMPEX (D62) и Kp-поправке 0.92°/Kp (D36): 63.44° − 8.28° = 55.16°.
    assert cfg.kp_assumed_when_missing == 9.0
    r30 = math.sqrt(30.0**2 + 2.0 * 30.0 * cfg.proton_rest_energy_mev) / 1000.0
    quiet = math.degrees(math.acos(((r30 + cfg.cutoff_offset_gv) / cfg.cutoff_constant_gv) ** 0.25))
    assert cfg.kp_suppression_deg_per_kp is not None
    storm = quiet - 9.0 * cfg.kp_suppression_deg_per_kp
    assert 55.1 < storm < 55.2
    in_window = [i for i, t in enumerate(traj.times) if WINDOW.start <= t < WINDOW.end]
    expected = sum(1 for i in in_window if abs(float(traj.mlat_deg[i])) >= storm)
    assert proxy.metrics["access_minutes"] == sep.metrics["access_minutes"] == float(expected) > 0.0
    # Текст ограничения называет число, а не шаблон подстановки.
    no_kp = [lim for lim in proxy.limitations if lim.startswith("Kp недоступен")]
    assert no_kp == [LIMIT_NO_KP.format(kp=9.0)] and "по Kp 9," in no_kp[0]
    assert not any("{" in lim for lim in proxy.limitations)
    assert all("принят по Kp 9" in i.note for i in proxy.intervals)


def test_missing_line_with_a_few_samples_carries_no_dose(tmp_path: Path) -> None:
    store = seed(tmp_path)
    cfg = load_sep_config()
    traj = trajectory(store, NIGHT)
    result = DoseProxyLine(load_dose_proxy_config(), cfg).assess(NIGHT, traj, AsOf(store, CUTOFF))
    sep = SepLine(cfg).assess(NIGHT, traj, AsOf(store, CUTOFF))
    # К 03:00 доступен только ряд за 10 мая (выпуск 11 мая 00:05): последний пятиминутный отсчёт 23:55 при допуске
    # 10 мин покрывает минуты 00:00–00:05, то есть 6 из 360. Покрытие ниже 0.8 даёт missing, доза за окно по шести
    # минутам была бы не нулём обстановки, а незнанием: числа дозы нет.
    assert result.availability == "missing"
    assert result.metrics["coverage"] * 360.0 == 6.0
    assert any(i.level != "unknown" for i in result.intervals)
    assert not any(key in result.metrics for key in DOSE_METRICS)
    assert result.metrics["access_minutes"] == sep.metrics["access_minutes"]
    assert PROXY_NOTE in result.limitations
