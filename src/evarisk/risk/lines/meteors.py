import tomllib
from bisect import bisect_right
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from pydantic import model_validator

from evarisk.orbit.radiant import radiant_unit_gcrs, radiant_visible
from evarisk.orbit.sun import solar_longitude_deg
from evarisk.orbit.trajectory import Trajectory
from evarisk.parsers.meo_flux import MeoFlux, parse_meo_flux
from evarisk.sources.meteor_docs import flux_product
from evarisk.store.asof import AsOf
from evarisk.store.raw import RawRecord
from evarisk.store.views import provenance_of, read_cached
from evarisk.timeutil import ensure_utc
from evarisk.types import Confidence, Frozen, Interval, Level, LineAssessment, Provenance, Window

DOC_SOURCE = "meteor_docs"
LIMIT_STATISTICAL = (
    "почасовой прогноз NASA MEO типичной активности потоков, не наблюдение; неожиданные вспышки активности возможны"
)
LIMIT_RELATIVE = (
    "коэффициент это поток метеорных потоков относительно спорадического фона для частиц, пробивающих скафандр; "
    "абсолютный поток и вероятность пробоя не оцениваются"
)
LIMIT_NO_ABERRATION = "радиант геоцентрический, аберрация на скорость станции (до 12° для медленных потоков) не учтена"
LIMIT_SHIELDING = (
    "экранирование Землёй учитывается, только когда радианты всех активных потоков ниже горизонта: вклад отдельных "
    "потоков в почасовой коэффициент MEO не разделён; поток без радианта в прогнозе MEO считается видимым"
)
LIMIT_NO_FORECAST = (
    "машиночитаемого прогноза NASA MEO на {years} нет: файлы почасовых данных опубликованы для прогнозов 2024–2026 "
    "годов, прогнозы более ранних лет вышли только в PDF"
)
LIMIT_PARTIAL = "часть окна вне почасового прогноза MEO: эти минуты без уровня"
LIMIT_DOCUMENT_LATER = (
    "прогноз NASA MEO доступен с {available:%Y-%m-%d}, позже отсечения: линия метеоров не считается и в "
    "сравнении окон не участвует (D65)"
)
CONFIDENCE_NOTE = "почасовой прогноз MEO типичной активности; экранирование только по радиантам шести крупных потоков"
_DEG_PER_DAY = 0.98564736
_TROPICAL_YEAR_DAYS = 365.2422
_DEFAULT_STEP = timedelta(minutes=1)
_HOUR = timedelta(hours=1)
_FLUX_CACHE: dict[str, MeoFlux] = {}


class Shower(Frozen):
    code: str
    name: str
    peak_utc: datetime
    zhr_max: float
    radiant_ra_deg: float | None = None
    radiant_dec_deg: float | None = None
    speed_kms: float | None = None
    start: date
    end: date
    highly_active: bool
    source: str

    @model_validator(mode="after")
    def _consistent(self) -> "Shower":
        if not self.start <= self.peak_utc.date() <= self.end:
            raise ValueError(f"{self.code}: максимум вне периода активности")
        if (self.radiant_ra_deg is None) != (self.radiant_dec_deg is None):
            raise ValueError(f"{self.code}: радиант задан одной координатой")
        return self


class MeteorConfig(Frozen):
    suit_energy_joule: float
    shield_radius_km: float = 6471.0
    elevated_factor: float
    high_factor: float
    activity_calendar_year: int
    showers: list[Shower]
    sources: dict[str, str]


def load_meteor_config(path: Path = Path("config/meteors.toml")) -> MeteorConfig:
    with path.open("rb") as fh:
        return MeteorConfig(**tomllib.load(fh))


def same_solar_longitude(t_ref: datetime, years: int) -> datetime:
    """Момент через `years` лет с той же долготой Солнца, что у `t_ref`.

    Годовые потоки привязаны к солнечной долготе; первое приближение это тропический год, затем поправка
    по разности долгот со скоростью 0.9856 °/сут.
    """
    target = solar_longitude_deg(t_ref)
    t = t_ref + timedelta(days=_TROPICAL_YEAR_DAYS * years)
    for _ in range(3):
        diff = (target - solar_longitude_deg(t) + 180.0) % 360.0 - 180.0
        t += timedelta(days=diff / _DEG_PER_DAY)
    return t


@lru_cache(maxsize=256)
def activity_bounds(shower: Shower, years: int) -> tuple[datetime, datetime, datetime]:
    """Начало, максимум и конец активности, перенесённые на `years` лет по солнечной долготе.

    Начало это начало суток `start`, конец это конец суток `end` по UTC.
    """
    start = datetime.combine(shower.start, time(0), tzinfo=UTC)
    end = datetime.combine(shower.end + timedelta(days=1), time(0), tzinfo=UTC)
    if years == 0:
        return start, shower.peak_utc, end
    return (
        same_solar_longitude(start, years),
        same_solar_longitude(shower.peak_utc, years),
        same_solar_longitude(end, years),
    )


def _profile(t: datetime, start: datetime, peak: datetime, end: datetime) -> float:
    if t <= start or t >= end:
        return 0.0
    if t <= peak:
        return (t - start) / (peak - start)
    return (end - t) / (end - peak)


def activity_fraction(shower: Shower, t: datetime) -> float:
    """Кусочно-линейный профиль: 1.0 в максимуме, 0 на границах периода активности.

    В уровень не входит (уровень задаёт коэффициент MEO, D67): профиль больше нуля означает, что поток активен и
    его радиант участвует в экранировании. Для года, отличного от года максимума, границы переносятся по
    солнечной долготе; проверяются соседние годы, так как период может пересекать границу года (Квадрантиды).
    """
    t = ensure_utc(t)
    years = t.year - shower.peak_utc.year
    return max(_profile(t, *activity_bounds(shower, dy)) for dy in (years - 1, years, years + 1))


def interpolate_hourly(times: list[datetime], values: NDArray[np.float64], t: datetime) -> float:
    """Значение в момент `t` линейно между соседними часовыми отсчётами; вне ряда и в разрыве больше часа NaN."""
    i = bisect_right(times, t) - 1
    if i < 0:
        return float("nan")
    if times[i] == t:
        return float(values[i])
    if i + 1 >= len(times) or times[i + 1] - times[i] > _HOUR:
        return float("nan")
    share = (t - times[i]) / (times[i + 1] - times[i])
    return float(values[i] + share * (values[i + 1] - values[i]))


def _grid_step(times: list[datetime]) -> timedelta:
    return times[1] - times[0] if len(times) > 1 else _DEFAULT_STEP


def _years(window: Window) -> list[int]:
    return list(range(window.start.year, (window.end - timedelta(microseconds=1)).year + 1))


class MeteorLine:
    name = "meteor_showers"
    mechanism = "meteoroids"

    def __init__(self, cfg: MeteorConfig) -> None:
        self.cfg = cfg
        self._radiants: dict[str, NDArray[np.float64]] = {
            s.code: radiant_unit_gcrs(s.radiant_ra_deg, s.radiant_dec_deg)
            for s in cfg.showers
            if s.radiant_ra_deg is not None and s.radiant_dec_deg is not None
        }

    def _level(self, factor: float) -> Level:
        if np.isnan(factor):
            return "unknown"
        if factor >= self.cfg.high_factor:
            return "high"
        if factor >= self.cfg.elevated_factor:
            return "elevated"
        return "none"

    def _rule(self) -> str:
        cfg = self.cfg
        return (
            f"коэффициент усиления NASA MEO для частиц {cfg.suit_energy_joule:g} Дж (0.04 см, пробивают скафандр) "
            f"из flux_data.txt, линейно между часовыми отсчётами; ноль, когда радианты всех активных потоков ниже "
            f"опущенного горизонта сферы {cfg.shield_radius_km:.0f} км; elevated при коэффициенте >= "
            f"{cfg.elevated_factor:g}, high при >= {cfg.high_factor:g} (наше правило, D55, D67)"
        )

    def _missing(self, limitations: list[str], reason: str) -> LineAssessment:
        return LineAssessment(
            line=self.name,
            mechanism=self.mechanism,
            intervals=(),
            availability="missing",
            confidence=Confidence(grade="low", depends_on="нет почасового прогноза MEO, доступного на момент запроса"),
            limitations=tuple(dict.fromkeys([*limitations, reason])),
        )

    def _published_later(self, limitations: list[str], available: datetime) -> LineAssessment:
        """Прогноз есть в хранилище, но стал доступен позже отсечения: линия вне сравнения окон (D65)."""
        return LineAssessment(
            line=self.name,
            mechanism=self.mechanism,
            intervals=(),
            availability="unusable_for_replay",
            confidence=Confidence(grade="low", depends_on="годовой прогноз MEO выпущен позже отсечения"),
            limitations=tuple(dict.fromkeys([*limitations, LIMIT_DOCUMENT_LATER.format(available=available)])),
        )

    def _series(
        self, asof: AsOf, years: list[int]
    ) -> tuple[list[datetime], NDArray[np.float64], list[RawRecord], list[int], list[datetime]]:
        """Почасовой коэффициент для энергии скафандра по прогнозам на годы окна.

        Возвращает времена, значения, использованные записи, годы без прогноза и времена доступности прогнозов,
        вышедших позже отсечения. Строки файла года Y берутся для моментов года Y; хвост файла за первые часы
        следующего года используется, только если прогноза на следующий год нет.
        """
        by_time: dict[datetime, float] = {}
        own: dict[datetime, float] = {}
        used: list[RawRecord] = []
        absent: list[int] = []
        later: list[datetime] = []
        for year in years:
            record = asof.latest(DOC_SOURCE, flux_product(year))
            if record is None:
                published = [r.published_at for r in asof.aggregates(DOC_SOURCE, flux_product(year)) if r.published_at]
                if asof.t is not None and published:
                    later.append(min(published))
                else:
                    absent.append(year)
                continue
            flux = read_cached(asof, record, _FLUX_CACHE, parse_meo_flux)
            if flux is None:
                absent.append(year)
                continue
            used.append(record)
            column = flux.factors[:, flux.column(self.cfg.suit_energy_joule)]
            for moment, value in zip(flux.times, column, strict=True):
                by_time.setdefault(moment, float(value))
                if moment.year == flux.year:
                    own[moment] = float(value)
        by_time.update(own)
        times = sorted(by_time)
        return times, np.asarray([by_time[m] for m in times], dtype=np.float64), used, absent, later

    def _shielded(self, times: list[datetime], r_km: NDArray[np.float64]) -> tuple[NDArray[np.bool_], list[str]]:
        """Минуты, когда радианты всех активных потоков ниже горизонта, и коды потоков, активных в окне."""
        cfg = self.cfg
        any_active = np.zeros(len(times), dtype=bool)
        all_hidden = np.ones(len(times), dtype=bool)
        active: list[str] = []
        for shower in cfg.showers:
            mask = np.asarray([activity_fraction(shower, t) > 0.0 for t in times], dtype=bool)
            if not mask.any():
                continue
            active.append(shower.code)
            any_active |= mask
            radiant = self._radiants.get(shower.code)
            if radiant is None:
                all_hidden &= ~mask
                continue
            visible = radiant_visible(r_km, radiant, cfg.shield_radius_km)
            all_hidden &= ~(mask & visible)
        return any_active & all_hidden, active

    def assess(self, window: Window, trajectory: Trajectory, asof: AsOf) -> LineAssessment:
        cfg = self.cfg
        limitations: list[str] = [LIMIT_STATISTICAL, LIMIT_RELATIVE, LIMIT_NO_ABERRATION, LIMIT_SHIELDING]
        limitations.extend(trajectory.limitations)
        series_times, series, used, absent, later = self._series(asof, _years(window))
        if absent:
            limitations.append(LIMIT_NO_FORECAST.format(years=", ".join(f"{y} год" for y in absent)))
        if not used:
            if later:
                return self._published_later(limitations, min(later))
            return self._missing(limitations, LIMIT_NO_FORECAST.format(years=", ".join(map(str, _years(window)))))
        in_window = [i for i, t in enumerate(trajectory.times) if window.start <= t < window.end]
        if not in_window:
            raise ValueError("траектория не покрывает окно")
        times = [trajectory.times[i] for i in in_window]
        raw = np.asarray([interpolate_hourly(series_times, series, t) for t in times], dtype=np.float64)
        shielded, active = self._shielded(times, trajectory.r_km[in_window])
        factor = np.where(shielded & ~np.isnan(raw), 0.0, raw)
        if np.isnan(factor).any():
            limitations.append(LIMIT_PARTIAL)
        levels: list[Level] = [self._level(float(x)) for x in factor]
        provenance: tuple[Provenance, ...] = (*(provenance_of(r) for r in used), trajectory.provenance)
        step = _grid_step(trajectory.times)
        rule = self._rule()
        showers = f"активные потоки: {', '.join(active)}" if active else "активных потоков нет"
        intervals: list[Interval] = []
        run_start = 0
        for j in range(1, len(in_window) + 1):
            if j == len(in_window) or levels[j] != levels[run_start]:
                part = factor[run_start:j]
                known = part[~np.isnan(part)]
                value = f"коэффициент {known.min():.2f}–{known.max():.2f}" if known.size else "коэффициента нет"
                intervals.append(
                    Interval(
                        start=times[run_start],
                        end=min(times[j - 1] + step, window.end),
                        level=levels[run_start],
                        kind="external_forecast",
                        provenance=provenance,
                        rule=rule,
                        note=f"{value}; экранировано Землёй {int(shielded[run_start:j].sum())} мин; {showers}",
                    )
                )
                run_start = j
        known_all = factor[~np.isnan(factor)]
        raw_known = raw[~np.isnan(raw)]
        metrics: dict[str, float] = {
            "shielded_minutes": float(shielded.sum()),
            "active_showers": float(len(active)),
            "suit_energy_joule": cfg.suit_energy_joule,
        }
        # Максимум коэффициента есть, только если в окне есть минуты с прогнозом: без них метрики нет.
        if known_all.size:
            metrics["max_factor"] = float(known_all.max())
            metrics["max_factor_unshielded"] = float(raw_known.max())
        return LineAssessment(
            line=self.name,
            mechanism=self.mechanism,
            intervals=tuple(intervals),
            availability="available",
            confidence=Confidence(grade="medium", depends_on=CONFIDENCE_NOTE),
            limitations=tuple(dict.fromkeys(limitations)),
            metrics=metrics,
        )
