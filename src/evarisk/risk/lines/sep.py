import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import numpy as np
from numpy.typing import NDArray

from evarisk.orbit.trajectory import Trajectory
from evarisk.risk.config import SepConfig
from evarisk.store.asof import AsOf
from evarisk.store.views import KpSeries, ProtonSeries, kp_series, proton_series, unreadable_records
from evarisk.store.views_donki import (
    SepEventKnown,
    SepStateKnown,
    cme_kp_forecast,
    sep_state_as_known,
    skipped_messages,
)
from evarisk.store.views_kp import kp_as_known, kp_forecast, proton_event_probability
from evarisk.types import Availability, Confidence, Interval, Kind, Level, LineAssessment, Provenance, Window

LIMIT_S_SCALE = "шкала S по ≥10 MeV описывает обстановку на геостационарной орбите, не дозу на МКС"
LIMIT_NO_SUPPRESSION = "поправка на бурю не применена: коэффициент Kp-формы не задан, а Dst в хранилище нет (D36)"
LIMIT_KP_UNVERIFIED = "поправка на бурю по Kp: коэффициент оценён по рис. 6 Leske 2001, не сверен (D36)"
LIMIT_NO_KP = (
    "Kp недоступен: доступ частиц на таких минутах принят консервативно по Kp {kp:g}, наибольшему снижению широты "
    "обрезания; спокойный порог давал бы нулевой доступ при любом потоке (D54)"
)
NOTE_KP_ASSUMED = "неизвестен, доступ принят по Kp {kp:g} (консервативно, D54)"
NOTE_KP_LAST_FORECAST = "наблюдённого значения к отсечению нет, взят последний прогноз на интервал"
LIMIT_NO_DST = "Dst в хранилище нет, применена Kp-форма поправки (D36)"
LIMIT_NO_PROTONS = "нет данных о потоке протонов на момент отсечения"
LIMIT_KP_AS_KNOWN = (
    "Kp до отсечения взят из датированных продуктов SWPC (прошедшие ячейки 3-day forecast, DONKI GST, "
    "максимум за 24 ч), не из definitive GFZ (D21, D39)"
)
LIMIT_KP_FORECAST = (
    "Kp после отсечения взят из внешнего прогноза SWPC ({version}); "
    "интервалы с таким Kp помечены external_forecast (D39)"
)
LIMIT_PERSISTENCE = (
    "поток протонов после последнего отсчёта {last} UTC принят постоянным (персистентность) "
    "не дальше {hours:g} ч (наше правило, D39)"
)
LIMIT_BEYOND_HORIZON = "поток протонов за горизонтом персистентности {hours:g} ч не прогнозируется (D39)"
LIMIT_OUTLOOK_LEVEL = (
    "за горизонтом персистентности уровень окружения взят из внешнего прогноза вероятности протонного события (D42)"
)
LIMIT_MARGINAL_PASSES = (
    "пограничные витки при прогнозном Kp (до широты обрезания меньше {unit:.2f}°, одна единица Kp): {passes}; "
    "откроются, если Kp окажется на единицу выше прогноза (D49)"
)
SKIPPED_SHOWN = 3
_P100_MEV = 100.0
LIMIT_RECORDS_UNREADABLE = (
    "записи источников не разобраны и пропущены, их сведения в расчёт не вошли: {count}, первые: {shown}"
)
LIMIT_DONKI_SKIPPED = (
    "сообщения DONKI в незнакомой формулировке пропущены, их сведения в расчёт не вошли: {count}, первые: {shown}"
)
LIMIT_CONDITIONAL_EXPOSURE = (
    "high на минутах доступа это условная экспозиция: при реализации прогноза или идущего по DONKI события трасса "
    "открыта для протонов ≥{mev:g} MeV, поток на эти минуты не измерен (D47)"
)
LIMIT_OUTLOOK = (
    "вероятность протонного события на {day}: {percent} % ({product}, выпуск {issued} UTC), "
    "порог {threshold:g} % (наше правило, D39)"
)
LIMIT_NO_OUTLOOK = "нет прогноза вероятности протонного события на {day} на момент отсечения"
LIMIT_DONKI_STATE = "уровень окружения по уведомлениям DONKI о пересечении порогов, без измеренного спектра (D44)"
LIMIT_DONKI_EVENT = (
    "DONKI: событие ≥{energy:g} MeV > {threshold:g} pfu с {since} UTC (выпуск {issued}, последнее подтверждение "
    "{confirmed}) принято идущим {hours:g} ч после последнего подтверждающего сообщения, пока измеренный поток не ниже "
    "порога (наше правило, D60)"
)
LIMIT_DONKI_HORIZON = (
    "состояние события DONKI ≥{energy:g} MeV с {since} UTC после {until} UTC не известно: срок удержания события "
    "истёк, сообщений о продолжении или окончании к отсечению нет, дальше действует D42 (D60)"
)
LIMIT_NO_DONKI = (
    "уведомлений DONKI о пересечении порогов протонов, действующих на моменты окна, на момент отсечения нет"
)
LIMIT_CME_KP = (
    "Kp после отсечения на моментах ожидаемого прихода CME взят как максимум из прогноза SWPC и прогноза прихода CME "
    "DONKI/WSA-ENLIL (консервативно, D44); интервалы с таким Kp помечены external_forecast"
)
CONFIDENCE_FORECAST = (
    "после отсечения уровень или доступ опираются на прогноз Kp SWPC, персистентность потока "
    "или прогноз вероятности протонного события (D39)"
)
CONFIDENCE_OUTLOOK_ONLY = (
    "уровень окружения только по внешнему прогнозу вероятности SWPC, измерений потока в окне нет (D42)"
)
CONFIDENCE_DONKI = (
    "уровень окружения по уведомлениям DONKI о пересечении порогов и внешним прогнозам, "
    "измерений потока в окне нет (D44)"
)
FLUX_OBSERVED = "наблюдение"
FLUX_NONE = "нет отсчёта"
FLUX_OUTLOOK = "внешний прогноз вероятности {percent} %"
NOTE_ACCESS_UNMEASURED = "доступ есть, поток не измерен: high при реализации прогноза (D47)"
NOTE_ACCESS_DONKI = "доступ есть, спектр по DONKI не известен: high при идущем событии (D47)"
NOTE_DONKI_EXPIRED = (
    "событие DONKI без объявленного окончания: срок удержания истёк, уровень не ниже elevated до 96 ч от последнего "
    "подтверждения (D64)"
)
NOTE_DONKI = "DONKI: ≥{energy:g} MeV > {threshold:g} pfu с {since} (выпуск {issued})"
NOTE_KP_ABOVE_SWPC = "выше прогноза SWPC ({swpc})"
NOTE_KP_NOT_BELOW_CME = "не ниже прогноза прихода CME ({cme})"
KP_NONE = "недоступен"

_LOOKBACK = timedelta(hours=6)
_STALE_AFTER = timedelta(hours=2)
_SAMPLE_TOLERANCE = timedelta(minutes=10)
_KP_STEP = timedelta(hours=3)
_DEFAULT_STEP = timedelta(minutes=1)
_MIN_COVERAGE = 0.8
# Событие DONKI с истёкшим горизонтом упоминается в ограничениях, если горизонт истёк не раньше суток до окна.
_EXPIRED_MENTION = timedelta(hours=24)
_QUIET_LEVELS: frozenset[Level] = frozenset({"none", "unknown"})
_ORDER: dict[Level, int] = {"unknown": -1, "none": 0, "elevated": 1, "high": 2, "critical": 3}

Outlook = tuple[int, Provenance] | None


def _conditional_level(access: bool) -> Level:
    """Уровень точки без измеренного потока при прогнозе или идущем событии: high на минутах доступа (D47)."""
    return "high" if access else "elevated"


def rigidity_gv(kinetic_mev: float, rest_mev: float) -> float:
    return math.sqrt(kinetic_mev**2 + 2.0 * kinetic_mev * rest_mev) / 1000.0


def quiet_cutoff_latitude_deg(rigidity_gv_value: float, cfg: SepConfig) -> float:
    """Спокойная широта обрезания по фиту измерений SAMPEX: R = C·cos⁴Λ − R0 (Ogliore et al. 2001, D62)."""
    share = (rigidity_gv_value + cfg.cutoff_offset_gv) / cfg.cutoff_constant_gv
    return math.degrees(math.acos(min(1.0, max(0.0, share)) ** 0.25))


def cutoff_rigidity_gv(latitude_deg: float, cfg: SepConfig) -> float:
    """Жёсткость обрезания на инвариантной широте по тому же фиту; ниже нуля жёсткости нет."""
    return max(0.0, cfg.cutoff_constant_gv * math.cos(math.radians(latitude_deg)) ** 4 - cfg.cutoff_offset_gv)


def cutoff_latitude_deg(
    rigidity_gv_value: float, cfg: SepConfig, *, kp: float | None = None, dst: float | None = None
) -> float:
    quiet = quiet_cutoff_latitude_deg(rigidity_gv_value, cfg)
    if dst is not None and cfg.dst_suppression_deg_per_nt is not None:
        return max(0.0, quiet + dst * cfg.dst_suppression_deg_per_nt)
    if kp is not None and cfg.kp_suppression_deg_per_kp is not None:
        return max(0.0, quiet - kp * cfg.kp_suppression_deg_per_kp)
    return quiet


def env_level(p10_pfu: float, cfg: SepConfig) -> Level:
    if math.isnan(p10_pfu):
        return "unknown"
    if p10_pfu >= cfg.s3_pfu:
        return "critical"
    if p10_pfu >= cfg.s2_pfu:
        return "high"
    if p10_pfu >= cfg.s1_pfu:
        return "elevated"
    return "none"


def _nearest(series_times: list[datetime], values: NDArray[np.float64], t: datetime, tolerance: timedelta) -> float:
    if not series_times:
        return float("nan")
    stamps = np.asarray([x.timestamp() for x in series_times], dtype=np.float64)
    i = int(np.argmin(np.abs(stamps - t.timestamp())))
    if abs(series_times[i] - t) > tolerance:
        return float("nan")
    return float(values[i])


def _kp_at(kp: KpSeries | None, t: datetime) -> tuple[float | None, str]:
    if kp is None:
        return None, ""
    for i, start in enumerate(kp.starts):
        if start <= t < start + _KP_STEP:
            return float(kp.kp[i]), kp.notes[i] if kp.notes else ""
    return None, ""


def _finite_max(values: list[float]) -> float | None:
    finite = [v for v in values if not math.isnan(v)]
    return max(finite) if finite else None


def _grid_step(times: list[datetime]) -> timedelta:
    return times[1] - times[0] if len(times) > 1 else _DEFAULT_STEP


def _issued(prov: Provenance) -> str:
    return "неизвестен" if prov.published_at is None else f"{prov.published_at:%Y-%m-%d %H:%M}"


def _donki_note(event: SepEventKnown) -> str:
    return NOTE_DONKI.format(
        energy=event.energy_mev,
        threshold=event.threshold_pfu,
        since=f"{event.since:%Y-%m-%d %H:%M}",
        issued=f"{event.issued_at:%Y-%m-%d %H:%M}",
    )


@dataclass(frozen=True)
class _DonkiAt:
    """Состояние по DONKI в точке: идущие события в пределах горизонта и события, чей горизонт истёк."""

    p10: SepEventKnown | None
    p100: SepEventKnown | None
    expired: tuple[SepEventKnown, ...]

    @property
    def any(self) -> bool:
        return self.p10 is not None or self.p100 is not None


_NO_DONKI = _DonkiAt(None, None, ())


@dataclass(frozen=True)
class _Observed:
    times: list[datetime]
    p10: NDArray[np.float64]
    p30: NDArray[np.float64]
    p100: NDArray[np.float64]

    @property
    def last(self) -> int | None:
        finite = [i for i in range(len(self.times)) if not math.isnan(float(self.p10[i]))]
        return finite[-1] if finite else None


def _observed(protons: ProtonSeries | None, t_cut: datetime | None) -> _Observed:
    if protons is None:
        empty = np.asarray([], dtype=np.float64)
        return _Observed([], empty, empty, empty)
    mask = np.asarray([t_cut is None or t <= t_cut for t in protons.times], dtype=bool)
    times = [t for t, m in zip(protons.times, mask, strict=True) if m]
    return _Observed(times, protons.p10[mask], protons.p30[mask], protons.p100[mask])


@dataclass(frozen=True)
class KpAt:
    """Kp в момент траектории и его происхождение; `value` равно `None`, когда Kp неизвестен."""

    value: float | None
    note: str
    forecast: bool
    last_forecast: bool
    cme_used: bool
    cme_seen: bool


@dataclass(frozen=True)
class KpSources:
    """Ряды Kp, по которым линия SEP определяет доступ частиц на моментах окна.

    По архиву (`t_cut` равно `None`) это definitive GFZ. При отсечении до него действуют значения, известные к
    отсечению (D21, D39), и последний прогноз на интервал без наблюдённого значения (D54); после него прогноз SWPC,
    а на моментах ожидаемого прихода CME максимум из него и прогноза DONKI/WSA-ENLIL (D39, D44).
    """

    t_cut: datetime | None
    known: KpSeries | None
    forecast: KpSeries | None = None
    elapsed_forecast: KpSeries | None = None
    cme: KpSeries | None = None

    def at(self, t: datetime) -> KpAt:
        is_forecast = last_forecast = cme_used = cme_seen = False
        if self.t_cut is None or t <= self.t_cut:
            value, note = _kp_at(self.known, t)
            if value is None and self.t_cut is not None:
                value, fallback_note = _kp_at(self.elapsed_forecast, t)
                if value is not None:
                    note, is_forecast, last_forecast = f"{fallback_note}; {NOTE_KP_LAST_FORECAST}", True, True
        else:
            value, note = _kp_at(self.forecast, t)
            cme_val, cme_note = _kp_at(self.cme, t)
            if cme_val is not None:
                cme_seen = True
                if value is None:
                    value, note, cme_used = cme_val, cme_note, True
                elif cme_val > value:
                    note = f"{cme_note}; {NOTE_KP_ABOVE_SWPC.format(swpc=note)}"
                    value, cme_used = cme_val, True
                else:
                    note = f"{note}; {NOTE_KP_NOT_BELOW_CME.format(cme=cme_note)}"
            is_forecast = value is not None
        return KpAt(value, note, is_forecast, last_forecast, cme_used, cme_seen)


@dataclass(frozen=True)
class _Point:
    level: Level
    kind: Kind
    rule: str
    note: str
    access: bool
    p10: float
    p30: float
    p100: float
    kp_known: bool
    kp_forecast: bool
    kp_missing: bool
    flux_observed: bool
    unmeasured: bool
    persisted: bool
    beyond_horizon: bool
    outlook_level: bool
    conditional_high: bool
    t: datetime
    margin_deg: float
    after_last: bool
    donki_level: bool
    donki_p10: SepEventKnown | None
    donki_p100: SepEventKnown | None
    donki_expired: tuple[SepEventKnown, ...]
    donki_provenance: tuple[Provenance, ...]
    cme_kp: bool
    cme_seen: bool


class SepLine:
    name = "sep_protons"
    mechanism = "radiation_sep"

    def __init__(self, cfg: SepConfig) -> None:
        self.cfg = cfg
        self._r_suit = rigidity_gv(cfg.suit_threshold_mev, cfg.proton_rest_energy_mev)
        self._horizon = timedelta(hours=cfg.forecast_horizon_hours)

    def _margin_deg(self, mlat: float, kp: float | None) -> float:
        """Градусы до широты обрезания: положительный запас у закрытой точки, неположительный у открытой."""
        if math.isnan(mlat):
            return math.nan
        return cutoff_latitude_deg(self._r_suit, self.cfg, kp=kp) - abs(mlat)

    def has_access(self, mlat: float, kp: float | None) -> bool:
        margin = self._margin_deg(mlat, kp)
        return not math.isnan(margin) and margin <= 0.0

    def kp_sources(self, window: Window, asof: AsOf) -> KpSources:
        """Ряды Kp на окно в том виде, в каком их читает `assess`; линия прокси дозы берёт доступ по ним же (D40)."""
        start = window.start - max(_LOOKBACK, self._horizon)
        if asof.t is None:
            return KpSources(t_cut=None, known=kp_series(asof, start, window.end))
        return KpSources(
            t_cut=asof.t,
            known=kp_as_known(asof, start, window.end),
            forecast=kp_forecast(asof, start, window.end),
            elapsed_forecast=kp_forecast(asof, start, window.end, include_elapsed=True),
            cme=cme_kp_forecast(asof, start, window.end),
        )

    def access_kp(self, kp: KpAt) -> float:
        """Kp, по которому считается доступ: при неизвестном Kp это `kp_assumed_when_missing` (D54)."""
        return self.cfg.kp_assumed_when_missing if kp.value is None else kp.value

    def _marginal_passes(self, points: list[_Point]) -> list[tuple[datetime, float]]:
        """Закрытые витки при прогнозном Kp с запасом меньше одной единицы Kp: момент и величина минимума запаса.

        Серия это подряд идущие точки с запасом меньше единицы Kp, включая открытые; серия с открытой точкой это
        плечи открытого витка, а не пограничный виток, и в список не входит.
        """
        unit = self.cfg.kp_suppression_deg_per_kp
        if unit is None:
            return []
        passes: list[tuple[datetime, float]] = []
        current: tuple[datetime, float] | None = None
        open_in_run = False
        for p in [*points, None]:
            near = p is not None and p.kp_forecast and not math.isnan(p.margin_deg) and p.margin_deg < unit
            if p is not None and near:
                open_in_run = open_in_run or p.access
                if current is None or p.margin_deg < current[1]:
                    current = (p.t, p.margin_deg)
                continue
            if current is not None and not open_in_run:
                passes.append(current)
            current, open_in_run = None, False
        return passes

    def _point_level(self, p10: float, p30: float, p100: float, access: bool) -> Level:
        """Уровень точки (D63): high и critical только при геомагнитном доступе, то есть при экспозиции экипажа.

        Без доступа обстановка по шкале S даёт не выше elevated: станция закрыта магнитным полем. При доступе
        решает измеренный спектр независимо от шкалы S: поток ≥100 MeV не ниже 1 pfu это critical (порог ESPE),
        поток ≥30 MeV не ниже 1 pfu это high; обстановка S2 и выше без измеренного потока ≥30 MeV даёт high.
        """
        if access and not math.isnan(p100) and p100 >= self.cfg.p100_event_pfu:
            return "critical"
        if access and not math.isnan(p30) and p30 >= self.cfg.p30_event_pfu:
            return "high"
        env = env_level(p10, self.cfg)
        if env in _QUIET_LEVELS:
            return env
        if not access:
            return "elevated"
        if math.isnan(p30) and _ORDER[env] >= _ORDER["high"]:
            return "high"
        return "elevated"

    def _rule(self) -> str:
        cfg = self.cfg
        kp_part = "" if cfg.kp_suppression_deg_per_kp is None else f" минус {cfg.kp_suppression_deg_per_kp}°·Kp"
        return (
            f"обстановка по шкале S (p10: S1={cfg.s1_pfu}, S2={cfg.s2_pfu}, S3={cfg.s3_pfu} pfu) даёт elevated от S1; "
            "high и critical только при геомагнитном доступе, то есть при экспозиции экипажа (D63); "
            f"доступ: |mlat| >= arccos(((R+{cfg.cutoff_offset_gv})/{cfg.cutoff_constant_gv})^0.25) "
            f"при R({cfg.suit_threshold_mev:.0f} MeV)={self._r_suit:.3f} GV{kp_part} (фит SAMPEX, D62); "
            f"high при доступе и p30 >= {cfg.p30_event_pfu} pfu либо при S2 и выше без измеренного p30, "
            f"critical при доступе и p100 >= {cfg.p100_event_pfu} pfu"
        )

    def _exposure_rule(self) -> str:
        return (
            f"high на минутах доступа (порог {self.cfg.suit_threshold_mev:.0f} MeV, Kp по прогнозу): "
            "при реализации прогноза трасса открыта для протонов, поток не измерен (наше правило, D47)"
        )

    def _outlook_rule(self, percent: int, product: str) -> str:
        return (
            f"{product}: вероятность протонного события {percent} % >= "
            f"{self.cfg.proton_outlook_threshold_percent:g} % даёт elevated (наше правило, D39); "
            f"{self._exposure_rule()}"
        )

    def _outlook_level_rule(self, percent: int, prov: Provenance, level: Level) -> str:
        head = (
            f"уровень по внешнему прогнозу вероятности протонного события {percent} % "
            f"({prov.product}, выпуск {_issued(prov)} UTC): "
        )
        if level == "none":
            return f"{head}none при пороге {self.cfg.proton_outlook_threshold_percent:g} % (наше правило, D42)"
        return (
            f"{head}elevated при пороге {self.cfg.proton_outlook_threshold_percent:g} % (наше правило, D42); "
            f"{self._exposure_rule()}"
        )

    def _donki_rule(self) -> str:
        return (
            "по уведомлениям DONKI о пересечении порогов GOES: уровень не ниже elevated при идущем событии "
            f"≥10 MeV > 10 pfu, high на минутах доступа при идущем событии ≥10 MeV без измеренного спектра (D47), "
            f"critical при доступе и идущем событии ≥100 MeV > {self.cfg.p100_event_pfu:g} pfu "
            f"(порог ESPE); событие считается идущим {self.cfg.donki_p10_hold_hours:g} ч (≥10 MeV) и "
            f"{self.cfg.donki_p100_hold_hours:g} ч (≥100 MeV) после последнего подтверждающего сообщения, "
            "пока измеренный поток не ниже порога: сообщений об окончании DONKI почти не выпускает "
            "(наше правило, D60, D44)"
        )

    def _hold(self, event: SepEventKnown) -> timedelta:
        """Срок, на который событие считается идущим после последнего подтверждающего сообщения (D60)."""
        hours = self.cfg.donki_p100_hold_hours if event.energy_mev >= _P100_MEV else self.cfg.donki_p10_hold_hours
        return timedelta(hours=hours)

    def _ended_by_flux(self, event: SepEventKnown, observed: _Observed) -> bool:
        """Истина, когда три последних измеренных отсчёта позже последнего подтверждения ниже порога события.

        Правило трёх последовательных отсчётов GOES: SRAG AES-CHP-SW-002, с. 9–10 (D64). Отсчёты берутся по ряду
        той энергии, к которой относится событие; пропуски ряда в счёт не идут.
        """
        series = observed.p100 if event.energy_mev >= _P100_MEV else observed.p10
        later = [
            float(series[i])
            for i, t in enumerate(observed.times)
            if t > event.confirmed_at and not math.isnan(float(series[i]))
        ]
        needed = self.cfg.donki_end_samples
        return len(later) >= needed and all(value < event.threshold_pfu for value in later[-needed:])

    def _lingering(self, expired: tuple[SepEventKnown, ...], t: datetime) -> tuple[SepEventKnown, ...]:
        """События с истёкшим удержанием, моложе `donki_floor_hours` от подтверждения (D64); пусто, если таких нет."""
        limit = timedelta(hours=self.cfg.donki_floor_hours)
        return tuple(event for event in expired if t <= event.confirmed_at + limit)

    def _donki_at(self, states: list[SepStateKnown], t: datetime, observed: _Observed) -> _DonkiAt:
        for piece in states:
            if piece.start <= t < piece.end:
                active: list[SepEventKnown | None] = []
                expired: list[SepEventKnown] = []
                for event in (piece.p10_event, piece.p100_event):
                    if event is None or t < event.since or self._ended_by_flux(event, observed):
                        active.append(None)
                    elif t <= event.confirmed_at + self._hold(event):
                        active.append(event)
                    else:
                        active.append(None)
                        expired.append(event)
                return _DonkiAt(active[0], active[1], tuple(expired))
        return _NO_DONKI

    def _confidence(self, suppression_active: bool) -> Confidence:
        if not suppression_active:
            return Confidence(grade="low", depends_on="поправка на бурю не применена или Kp недоступен")
        if self.cfg.kp_suppression_verified:
            return Confidence(grade="high", depends_on="поток GOES, Kp и траектория доступны, коэффициент сверен")
        return Confidence(grade="medium", depends_on="коэффициент Kp-поправки оценён по рисунку статьи, не сверен")

    def _missing(self, limitations: list[str]) -> LineAssessment:
        return LineAssessment(
            line=self.name,
            mechanism=self.mechanism,
            intervals=(),
            availability="missing",
            confidence=Confidence(grade="low", depends_on="нет ряда протонов, доступного на момент запроса"),
            limitations=tuple(dict.fromkeys([*limitations, LIMIT_NO_PROTONS])),
        )

    def _point(
        self,
        t: datetime,
        mlat: float,
        t_cut: datetime | None,
        observed: _Observed,
        kp_sources: KpSources,
        donki: list[SepStateKnown],
        outlook: Outlook,
        base_rule: str,
    ) -> _Point:
        persisted = beyond = outlook_level = conditional = False
        last = observed.last
        after_last = last is None or t > observed.times[last]
        kp = kp_sources.at(t)
        kp_val, kp_note, kp_is_forecast = kp.value, kp.note, kp.forecast
        if t_cut is None or t <= t_cut:
            p10 = _nearest(observed.times, observed.p10, t, _SAMPLE_TOLERANCE)
            p30 = _nearest(observed.times, observed.p30, t, _SAMPLE_TOLERANCE)
            p100 = _nearest(observed.times, observed.p100, t, _SAMPLE_TOLERANCE)
            flux_note = FLUX_NONE if math.isnan(p10) else FLUX_OBSERVED
            outlook = None
        else:
            if last is not None and t - observed.times[last] <= self._horizon:
                p10, p30, p100 = float(observed.p10[last]), float(observed.p30[last]), float(observed.p100[last])
                persisted = True
                flux_note = f"персистентность отсчёта {observed.times[last]:%Y-%m-%d %H:%M}"
            else:
                p10 = p30 = p100 = float("nan")
                beyond = True
                flux_note = FLUX_NONE
        base_flux_note = flux_note
        kp_missing = kp_val is None
        kp_access = self.access_kp(kp)
        if kp_missing and t_cut is not None:
            kp_note = NOTE_KP_ASSUMED.format(kp=self.cfg.kp_assumed_when_missing)
        access = self.has_access(mlat, kp_access)
        level = self._point_level(p10, p30, p100, access)
        if t_cut is None and kp_missing and level not in _QUIET_LEVELS:
            # Разбор по архиву служит эталоном сверки: без Kp доступ не определён, консервативное Kp 9 здесь
            # превратило бы пробел в ряду Kp в ложную экспозицию по факту.
            level = "unknown"
        kind: Kind = "external_forecast" if kp_is_forecast else "computed"
        rule = base_rule
        threshold = self.cfg.proton_outlook_threshold_percent
        if beyond and outlook is not None:
            percent, prov = outlook
            level = _conditional_level(access) if percent >= threshold else "none"
            kind, outlook_level = "external_forecast", True
            rule = f"{base_rule}; {self._outlook_level_rule(percent, prov, level)}"
            flux_note = FLUX_OUTLOOK.format(percent=percent)
            if access and level == "high":
                flux_note, conditional = f"{flux_note}; {NOTE_ACCESS_UNMEASURED}", True
        elif outlook is not None and outlook[0] >= threshold and level in _QUIET_LEVELS:
            level, kind = _conditional_level(access), "external_forecast"
            rule = f"{base_rule}; {self._outlook_rule(outlook[0], outlook[1].product)}"
            if access:
                flux_note, conditional = f"{flux_note}; {NOTE_ACCESS_UNMEASURED}", True
        state = self._donki_at(donki, t, observed) if t_cut is not None and after_last else _NO_DONKI
        donki_level = False
        # Сообщения DONKI, которые задали уровень точки: провенанс интервала называет только их (D81).
        donki_events: list[SepEventKnown] = []
        if state.any:
            floor: Level = "critical" if state.p100 is not None and access else _conditional_level(access)
            if _ORDER[floor] > _ORDER[level] or (outlook_level and _ORDER[floor] >= _ORDER[level]):
                level, donki_level, outlook_level = floor, True, False
                kind = "external_forecast" if kp_is_forecast else "computed"
                rule = f"{base_rule}; {self._donki_rule()}"
                donki_events.extend(e for e in (state.p10, state.p100) if e is not None)
                parts = [base_flux_note, *(_donki_note(e) for e in donki_events)]
                conditional = access and floor == "high"
                if conditional:
                    parts.append(NOTE_ACCESS_DONKI)
                flux_note = "; ".join(parts)
        lingering = self._lingering(state.expired, t)
        if lingering and level in _QUIET_LEVELS and t_cut is not None:
            # Срок удержания события истёк, окончание не измерено и не объявлено: уровень не ниже elevated (D64).
            level = "elevated"
            donki_events.extend(lingering)
            rule = f"{base_rule}; {self._donki_rule()}"
            flux_note = f"{flux_note}; {NOTE_DONKI_EXPIRED}"
        return _Point(
            level=level,
            kind=kind,
            rule=rule,
            note="" if t_cut is None else f"Kp: {kp_note or KP_NONE}; поток: {flux_note}",
            access=access,
            p10=p10,
            p30=p30,
            p100=p100,
            kp_known=kp_val is not None and not kp_is_forecast and t_cut is not None,
            kp_forecast=kp_is_forecast,
            kp_missing=kp_val is None,
            flux_observed=flux_note == FLUX_OBSERVED,
            unmeasured=math.isnan(p10),
            persisted=persisted,
            beyond_horizon=beyond,
            outlook_level=outlook_level,
            conditional_high=conditional,
            t=t,
            margin_deg=self._margin_deg(mlat, kp_access),
            after_last=after_last and t_cut is not None,
            donki_level=donki_level,
            donki_p10=state.p10 if donki_level else None,
            donki_p100=state.p100 if donki_level else None,
            donki_expired=state.expired,
            donki_provenance=tuple(dict.fromkeys(prov for event in donki_events for prov in event.provenance)),
            cme_kp=kp.cme_used,
            cme_seen=kp.cme_seen,
        )

    def _donki_limitations(self, points: list[_Point], window: Window) -> list[str]:
        used: dict[tuple[float, datetime], SepEventKnown] = {}
        expired: dict[tuple[float, datetime], SepEventKnown] = {}
        for p in points:
            for event in (p.donki_p10, p.donki_p100):
                if event is not None:
                    used.setdefault((event.energy_mev, event.since), event)
            for event in p.donki_expired:
                if event.confirmed_at + self._hold(event) >= window.start - _EXPIRED_MENTION:
                    expired.setdefault((event.energy_mev, event.since), event)
        out: list[str] = []
        if used:
            out.append(LIMIT_DONKI_STATE)
        for event in sorted(used.values(), key=lambda e: (e.since, e.energy_mev)):
            out.append(
                LIMIT_DONKI_EVENT.format(
                    energy=event.energy_mev,
                    threshold=event.threshold_pfu,
                    since=f"{event.since:%Y-%m-%d %H:%M}",
                    issued=f"{event.issued_at:%Y-%m-%d %H:%M}",
                    confirmed=f"{event.confirmed_at:%Y-%m-%d %H:%M}",
                    hours=self._hold(event).total_seconds() / 3600.0,
                )
            )
        for event in sorted(expired.values(), key=lambda e: (e.since, e.energy_mev)):
            out.append(
                LIMIT_DONKI_HORIZON.format(
                    energy=event.energy_mev,
                    since=f"{event.since:%Y-%m-%d %H:%M}",
                    until=f"{event.confirmed_at + self._hold(event):%Y-%m-%d %H:%M}",
                )
            )
        if not used and not expired and any(p.after_last for p in points):
            out.append(LIMIT_NO_DONKI)
        return out

    def _limitations(
        self,
        base: list[str],
        protons: ProtonSeries | None,
        points: list[_Point],
        t_cut: datetime | None,
        observed: _Observed,
        forecast: KpSeries | None,
        outlooks: dict[date, Outlook],
        window: Window,
    ) -> tuple[str, ...]:
        limitations = [*base, protons.note if protons is not None else LIMIT_NO_PROTONS]
        kp_any = any(not p.kp_missing for p in points)
        if self.cfg.kp_suppression_deg_per_kp is None:
            limitations.append(LIMIT_NO_SUPPRESSION)
        else:
            if kp_any:
                limitations.append(LIMIT_NO_DST)
                if not self.cfg.kp_suppression_verified:
                    limitations.append(LIMIT_KP_UNVERIFIED)
            if any(p.kp_missing for p in points):
                limitations.append(LIMIT_NO_KP.format(kp=self.cfg.kp_assumed_when_missing))
        if t_cut is not None:
            if any(p.kp_known for p in points):
                limitations.append(LIMIT_KP_AS_KNOWN)
            if forecast is not None and any(p.kp_forecast for p in points):
                limitations.append(LIMIT_KP_FORECAST.format(version=forecast.data_version))
            last = observed.last
            if last is not None and any(p.persisted for p in points):
                stamp = f"{observed.times[last]:%Y-%m-%d %H:%M}"
                limitations.append(LIMIT_PERSISTENCE.format(last=stamp, hours=self.cfg.forecast_horizon_hours))
            if any(p.beyond_horizon for p in points):
                limitations.append(LIMIT_BEYOND_HORIZON.format(hours=self.cfg.forecast_horizon_hours))
            if any(p.outlook_level for p in points):
                limitations.append(LIMIT_OUTLOOK_LEVEL)
            if any(p.conditional_high for p in points):
                limitations.append(LIMIT_CONDITIONAL_EXPOSURE.format(mev=self.cfg.suit_threshold_mev))
            marginal = self._marginal_passes(points)
            if marginal and self.cfg.kp_suppression_deg_per_kp is not None:
                listed = ", ".join(f"{t:%H:%M} ({margin:.1f}°)" for t, margin in marginal)
                limitations.append(LIMIT_MARGINAL_PASSES.format(unit=self.cfg.kp_suppression_deg_per_kp, passes=listed))
            if any(p.cme_seen for p in points):
                limitations.append(LIMIT_CME_KP)
            limitations.extend(self._donki_limitations(points, window))
            for day, outlook in sorted(outlooks.items()):
                if outlook is None:
                    limitations.append(LIMIT_NO_OUTLOOK.format(day=day.isoformat()))
                    continue
                percent, prov = outlook
                limitations.append(
                    LIMIT_OUTLOOK.format(
                        day=day.isoformat(),
                        percent=percent,
                        product=prov.product,
                        issued=_issued(prov),
                        threshold=self.cfg.proton_outlook_threshold_percent,
                    )
                )
        return tuple(dict.fromkeys(limitations))

    def assess(
        self, window: Window, trajectory: Trajectory, asof: AsOf, points_out: list[_Point] | None = None
    ) -> LineAssessment:
        """Оценка линии; `points_out` получает поминутные точки расчёта для сверки экспозиции (`exposed_times`)."""
        t_cut = asof.t
        base_limitations: list[str] = [LIMIT_S_SCALE, *trajectory.limitations]
        lookback = max(_LOOKBACK, self._horizon)
        protons = proton_series(asof, window.start - lookback, window.end)
        if t_cut is None and protons is None:
            return self._missing(base_limitations)
        kp = self.kp_sources(window, asof)
        donki: list[SepStateKnown] = [] if t_cut is None else sep_state_as_known(asof, window.start, window.end)
        observed = _observed(protons, t_cut)
        in_window = [i for i, t in enumerate(trajectory.times) if window.start <= t < window.end]
        outlooks: dict[date, Outlook] = {}
        if t_cut is not None:
            for i in in_window:
                day = trajectory.times[i].date()
                if trajectory.times[i] > t_cut and day not in outlooks:
                    outlooks[day] = proton_event_probability(asof, day)
        base_rule = self._rule()
        points = [
            self._point(
                trajectory.times[i],
                float(trajectory.mlat_deg[i]),
                t_cut,
                observed,
                kp,
                donki,
                outlooks.get(trajectory.times[i].date()),
                base_rule,
            )
            for i in in_window
        ]
        if points_out is not None:
            points_out.extend(points)
        step = _grid_step(trajectory.times)
        step_minutes = step.total_seconds() / 60.0
        count = max(1, len(in_window))
        coverage = sum(1 for p in points if p.level != "unknown") / count
        forecast_points = sum(1 for p in points if p.kind == "external_forecast" or p.persisted)
        outlook_points = sum(1 for p in points if p.outlook_level)
        donki_points = sum(1 for p in points if p.donki_level)
        cme_points = sum(1 for p in points if p.cme_kp)
        availability: Availability = "available" if coverage >= _MIN_COVERAGE else "missing"
        last = observed.last
        if (
            availability == "available"
            and forecast_points == 0
            and donki_points == 0
            and last is not None
            and observed.times[last] < window.start - _STALE_AFTER
        ):
            availability = "stale"
        provenance_by_id: dict[str, Provenance] = {}
        sources: list[Provenance] = [
            *(protons.provenance if protons is not None else ()),
            *(kp.known.provenance if kp.known is not None else ()),
            *(kp.forecast.provenance if kp.forecast is not None else ()),
            *(kp.cme.provenance if kp.cme is not None and any(p.cme_seen for p in points) else ()),
            *(o[1] for o in outlooks.values() if o is not None),
            trajectory.provenance,
        ]
        for prov in sources:
            provenance_by_id.setdefault(prov.raw_id, prov)
        provenance = tuple(provenance_by_id.values())
        intervals: list[Interval] = []
        run_start = 0
        for j in range(1, len(in_window) + 1):
            if j == len(in_window) or _run_key(points[j]) != _run_key(points[run_start]):
                head = points[run_start]
                t0 = trajectory.times[in_window[run_start]]
                t1 = min(trajectory.times[in_window[j - 1]] + step, window.end)
                level = head.level
                if availability != "available" and level == "none":
                    level = "unknown"
                intervals.append(
                    Interval(
                        start=t0,
                        end=t1,
                        level=level,
                        kind=head.kind,
                        provenance=(*provenance, *head.donki_provenance),
                        rule=head.rule,
                        note=head.note,
                    )
                )
                run_start = j
        metrics: dict[str, float] = {
            "access_minutes": sum(1 for p in points if p.access) * step_minutes,
            "access_fraction": sum(1 for p in points if p.access) / count,
            "saa_minutes": sum(1 for i in in_window if bool(trajectory.in_saa[i])) * step_minutes,
            "coverage": coverage,
            "forecast_fraction": forecast_points / count,
            "outlook_fraction": outlook_points / count,
            "persistence_minutes": sum(1 for p in points if p.persisted) * step_minutes,
            "unmeasured_minutes": sum(1 for p in points if p.unmeasured) * step_minutes,
            "donki_state_minutes": donki_points * step_minutes,
            "cme_forecast_minutes": cme_points * step_minutes,
        }
        for key, values in (
            ("max_p10_pfu", [p.p10 for p in points]),
            ("max_p30_pfu", [p.p30 for p in points]),
            ("max_p100_pfu", [p.p100 for p in points]),
        ):
            m = _finite_max(values)
            if m is not None:
                metrics[key] = m
        measured = any(p.flux_observed or p.persisted for p in points)
        if donki_points > 0 and not measured:
            confidence = Confidence(grade="low", depends_on=CONFIDENCE_DONKI)
        elif outlook_points > 0 and not measured:
            confidence = Confidence(grade="low", depends_on=CONFIDENCE_OUTLOOK_ONLY)
        elif forecast_points > 0:
            confidence = Confidence(grade="low", depends_on=CONFIDENCE_FORECAST)
        else:
            kp_any = any(not p.kp_missing for p in points)
            confidence = self._confidence(self.cfg.kp_suppression_deg_per_kp is not None and kp_any)
        if donki_points > 0 and confidence.grade == "high":
            confidence = Confidence(grade="medium", depends_on=LIMIT_DONKI_STATE)
        return LineAssessment(
            line=self.name,
            mechanism=self.mechanism,
            intervals=tuple(intervals),
            availability=availability,
            confidence=confidence,
            limitations=(
                *self._limitations(base_limitations, protons, points, t_cut, observed, kp.forecast, outlooks, window),
                *_skipped_limitation(asof, window),
            ),
            metrics=metrics,
        )


def _run_key(p: _Point) -> tuple[Level, Kind, str, str, tuple[Provenance, ...]]:
    """Точки с одинаковым ключом сливаются в один интервал: уровень, вид, правило, заметка и сообщения DONKI."""
    return p.level, p.kind, p.rule, p.note, p.donki_provenance


EXPOSURE_LEVELS: frozenset[Level] = frozenset({"high", "critical"})


def exposed_times(points: list[_Point]) -> frozenset[datetime]:
    """Моменты трассы с геомагнитным доступом и уровнем high или critical.

    Это экспозиция экипажа: протоны достают станцию. Минуты уровня high без доступа описывают обстановку на
    геостационарной орбите (шкала S), станция на них закрыта магнитным полем.
    """
    return frozenset(p.t for p in points if p.access and p.level in EXPOSURE_LEVELS)


def _skipped_limitation(asof: AsOf, window: Window) -> tuple[str, ...]:
    """Ограничения о пропущенных данных: сообщения DONKI в незнакомой формулировке и записи, отвергнутые разбором."""
    out: list[str] = []
    skipped = skipped_messages(asof, window.start, window.end)
    if skipped:
        shown = "; ".join(f"{u.message_type} {u.message_id} ({u.reason})" for u in skipped[:SKIPPED_SHOWN])
        out.append(LIMIT_DONKI_SKIPPED.format(count=len(skipped), shown=shown))
    unreadable = unreadable_records(asof, window.start, window.end)
    if unreadable:
        listed = "; ".join(
            f"{r.source}/{r.product} {r.raw_id[:8]} ({r.reason[:80]})" for r in unreadable[:SKIPPED_SHOWN]
        )
        out.append(LIMIT_RECORDS_UNREADABLE.format(count=len(unreadable), shown=listed))
    return tuple(out)


LOOKBACK = _LOOKBACK
STALE_AFTER = _STALE_AFTER
SAMPLE_TOLERANCE = _SAMPLE_TOLERANCE
MIN_COVERAGE = _MIN_COVERAGE


def nearest_index(series_times: list[datetime], t: datetime, tolerance: timedelta) -> int | None:
    if not series_times:
        return None
    stamps = np.asarray([x.timestamp() for x in series_times])
    i = int(np.argmin(np.abs(stamps - t.timestamp())))
    return i if abs(series_times[i] - t) <= tolerance else None


def kp_at(kp: KpSeries | None, t: datetime) -> float | None:
    return _kp_at(kp, t)[0]


def grid_step(times: list[datetime]) -> timedelta:
    return _grid_step(times)
