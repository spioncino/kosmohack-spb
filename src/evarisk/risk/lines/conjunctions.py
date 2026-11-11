import tomllib
from datetime import datetime, timedelta
from itertools import pairwise
from pathlib import Path

from evarisk.orbit.trajectory import Trajectory
from evarisk.parsers.socrates import Conjunction, SocratesReport, parse_socrates
from evarisk.store.asof import AsOf
from evarisk.store.views import provenance_of
from evarisk.types import Availability, Confidence, Frozen, Interval, Level, LineAssessment, Provenance, Window

SOURCE = "socrates"
PRODUCT = "conjunctions_iss"
LIMIT_NO_ARCHIVE = "архива SOCRATES нет, сближения только в текущем режиме"
LIMIT_OPERATIONAL = (
    "сближение это ограничение планирования: манёвр уклонения и укрытие экипажа невозможны при людях снаружи; "
    "сближение не задаёт вероятность попадания в космонавта (D30)"
)
LIMIT_MAXPROB = (
    "MAXPROB SOCRATES это максимум Pc по размеру ковариации при допущениях SOCRATES о её форме и размере объектов, "
    "по публичным элементам: консервативная оценка, не гарантия; выше порога правил полёта МКС означает только, что "
    "порог не исключён (D53)"
)
LIMIT_THRESHOLDS_UNVERIFIED = "пороги вероятности 1e-5 и 1e-4 правил полёта МКС ждут сверки человеком (D53)"
LIMIT_COORBITING = (
    "из отчёта исключены со-орбитальные объекты с относительной скоростью ниже {speed:g} км/с (пристыкованные и "
    "сближающиеся корабли): {names}, строк {rows} (D53)"
)
LIMIT_NO_REPORT = "нет отчёта SOCRATES на момент запроса"
LIMIT_PUBLIC_ELEMENTS = "дистанция и время сближения по публичным элементам GP без ковариаций, расчёт SOCRATES"
NOTE_OUTSIDE = "вне интервала расчёта SOCRATES"
NOTE_CLEAR = "сближений ближе порога расчёта не найдено"
NOTE_BELOW_YELLOW = "сближения с MAXPROB ниже жёлтого порога, значит и Pc ниже него"
CONFIDENCE_AVAILABLE = (
    "отчёт SOCRATES на 7 суток по публичным элементам GP; дистанция и MAXPROB без ковариаций операторов"
)
CONFIDENCE_NONE = "сближения на момент запроса неизвестны"
_LEVEL_ORDER: tuple[Level, ...] = ("none", "elevated", "high")


class ConjunctionConfig(Frozen):
    margin_minutes: float
    close_range_km: float
    coorbiting_rel_speed_kms: float
    yellow_probability: float
    red_probability: float
    thresholds_verified: bool
    max_report_age_hours: float
    sources: dict[str, str]


def load_conjunction_config(path: Path = Path("config/conjunctions.toml")) -> ConjunctionConfig:
    with path.open("rb") as fh:
        return ConjunctionConfig(**tomllib.load(fh))


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="seconds").replace("+00:00", "Z")


def conjunction_rule(c: Conjunction) -> str:
    return (
        f"{c.secondary_name} ({c.secondary_catnr}): TCA {_iso(c.tca)}, минимальная дистанция {c.min_range_km:.3f} км, "
        f"относительная скорость {c.rel_speed_kms:.3f} км/с, MAXPROB {c.max_probability:.3e} "
        f"(верхняя оценка Pc по публичным элементам, D53)"
    )


def is_coorbiting(c: Conjunction, cfg: ConjunctionConfig) -> bool:
    """Объект, идущий рядом со станцией: пристыкованный или сближающийся корабль, не опасное сближение."""
    return c.rel_speed_kms < cfg.coorbiting_rel_speed_kms


def conjunction_level(c: Conjunction, cfg: ConjunctionConfig) -> Level:
    """Уровень по MAXPROB как верхней оценке Pc: не исключён красный порог, не исключён жёлтый, иначе none."""
    if c.max_probability >= cfg.red_probability:
        return "high"
    if c.max_probability >= cfg.yellow_probability:
        return "elevated"
    return "none"


def conjunctions_touching(report: SocratesReport, window: Window, cfg: ConjunctionConfig) -> list[Conjunction]:
    """Сближения без со-орбитальных объектов, чей интервал [TCA − margin, TCA + margin] пересекает окно."""
    margin = timedelta(minutes=cfg.margin_minutes)
    found = [
        c
        for c in report.conjunctions
        if not is_coorbiting(c, cfg) and c.tca - margin < window.end and c.tca + margin > window.start
    ]
    return sorted(found, key=lambda c: c.tca)


class ConjunctionLine:
    name = "conjunctions"
    mechanism = "planning_conflict"

    def __init__(self, cfg: ConjunctionConfig, now: datetime | None = None) -> None:
        """`now` это момент запроса в текущем режиме: по нему проверяется давность отчёта SOCRATES."""
        self.cfg = cfg
        self.now = now
        self._margin = timedelta(minutes=cfg.margin_minutes)

    def _rule(self) -> str:
        return (
            f"интервал сближения [TCA − {self.cfg.margin_minutes:.0f} мин, TCA + {self.cfg.margin_minutes:.0f} мин] "
            f"(наше правило: время на решение об уклонении, D30); MAXPROB как верхняя оценка Pc: high при MAXPROB >= "
            f"{self.cfg.red_probability:g} (красный порог правил полёта МКС не исключён), elevated при MAXPROB >= "
            f"{self.cfg.yellow_probability:g} (жёлтый не исключён), ниже none; со-орбитальные объекты с относительной "
            f"скоростью < {self.cfg.coorbiting_rel_speed_kms:g} км/с исключены (D53); вне интервала расчёта SOCRATES "
            f"уровень unknown"
        )

    def _without_data(self, availability: Availability, limitations: list[str]) -> LineAssessment:
        return LineAssessment(
            line=self.name,
            mechanism=self.mechanism,
            intervals=(),
            availability=availability,
            confidence=Confidence(grade="low", depends_on=CONFIDENCE_NONE),
            limitations=tuple(dict.fromkeys(limitations)),
        )

    def _breakpoints(self, window: Window, report: SocratesReport, found: list[Conjunction]) -> list[datetime]:
        points = {window.start, window.end}
        for t in (report.interval_start, report.interval_stop):
            if window.start < t < window.end:
                points.add(t)
        for c in found:
            for t in (c.tca - self._margin, c.tca + self._margin):
                if window.start < t < window.end:
                    points.add(t)
        return sorted(points)

    def assess(self, window: Window, trajectory: Trajectory, asof: AsOf) -> LineAssessment:
        limitations: list[str] = [LIMIT_OPERATIONAL, LIMIT_MAXPROB, LIMIT_PUBLIC_ELEMENTS]
        if not self.cfg.thresholds_verified:
            limitations.append(LIMIT_THRESHOLDS_UNVERIFIED)
        if asof.t is not None:
            return self._without_data("unusable_for_replay", [LIMIT_NO_ARCHIVE, *limitations])
        rec = asof.latest(SOURCE, PRODUCT)
        if rec is None:
            return self._without_data("missing", [LIMIT_NO_REPORT, *limitations])
        report = parse_socrates(asof.read(rec).decode("utf-8", errors="replace"))
        provenance: tuple[Provenance, ...] = (provenance_of(rec),)
        rule = self._rule()
        if self.now is not None:
            age_hours = (self.now - report.data_as_of).total_seconds() / 3600.0
            if age_hours > self.cfg.max_report_age_hours:
                limitations.append(
                    f"отчёт SOCRATES на {_iso(report.data_as_of)}, давность {age_hours:.1f} ч больше "
                    f"{self.cfg.max_report_age_hours:g} ч: сближения на окно неизвестны, отчёт не выдаётся за текущий"
                )
                stale = Interval(
                    start=window.start,
                    end=window.end,
                    level="unknown",
                    kind="external_forecast",
                    provenance=provenance,
                    rule=rule,
                    note=f"отчёт SOCRATES устарел: давность {age_hours:.1f} ч",
                )
                return LineAssessment(
                    line=self.name,
                    mechanism=self.mechanism,
                    intervals=(stale,),
                    availability="stale",
                    confidence=Confidence(grade="low", depends_on=CONFIDENCE_NONE),
                    limitations=tuple(dict.fromkeys(limitations)),
                    metrics={"report_age_hours": age_hours},
                )
        covered = report.interval_start < window.end and report.interval_stop > window.start
        if not covered:
            limitations.append(
                f"окно вне интервала расчёта SOCRATES {_iso(report.interval_start)}–{_iso(report.interval_stop)}, "
                f"сближения на него неизвестны"
            )
            unknown = Interval(
                start=window.start,
                end=window.end,
                level="unknown",
                kind="external_forecast",
                provenance=provenance,
                rule=rule,
                note=NOTE_OUTSIDE,
            )
            return LineAssessment(
                line=self.name,
                mechanism=self.mechanism,
                intervals=(unknown,),
                availability="missing",
                confidence=Confidence(grade="low", depends_on=CONFIDENCE_NONE),
                limitations=tuple(dict.fromkeys(limitations)),
                metrics={"conjunctions_in_window": 0.0},
            )
        found = conjunctions_touching(report, window, self.cfg)
        coorbiting = [c for c in report.conjunctions if is_coorbiting(c, self.cfg)]
        if coorbiting:
            names = ", ".join(sorted({c.secondary_name for c in coorbiting}))
            limitations.append(
                LIMIT_COORBITING.format(speed=self.cfg.coorbiting_rel_speed_kms, names=names, rows=len(coorbiting))
            )
        points = self._breakpoints(window, report, found)
        intervals: list[Interval] = []
        for t0, t1 in pairwise(points):
            mid = t0 + (t1 - t0) / 2
            covering = [c for c in found if c.tca - self._margin <= mid < c.tca + self._margin]
            inside = report.interval_start <= mid <= report.interval_stop
            if covering:
                level: Level = max((conjunction_level(c, self.cfg) for c in covering), key=_LEVEL_ORDER.index)
                segment_rule = rule + "; " + "; ".join(conjunction_rule(c) for c in covering)
                listed = ", ".join(f"{c.secondary_name} ({c.secondary_catnr})" for c in covering)
                note = f"{NOTE_BELOW_YELLOW}: {listed}" if level == "none" else f"сближения: {listed}"
            elif inside:
                level, segment_rule, note = "none", rule, NOTE_CLEAR
            else:
                level, segment_rule, note = "unknown", rule, NOTE_OUTSIDE
            intervals.append(
                Interval(
                    start=t0,
                    end=t1,
                    level=level,
                    kind="external_forecast",
                    provenance=provenance,
                    rule=segment_rule,
                    note=note,
                )
            )
        metrics: dict[str, float] = {
            "conjunctions_in_window": float(len(found)),
            "coorbiting_rows_in_report": float(len(coorbiting)),
        }
        if found:
            metrics["min_range_km"] = min(c.min_range_km for c in found)
        return LineAssessment(
            line=self.name,
            mechanism=self.mechanism,
            intervals=tuple(intervals),
            availability="available",
            confidence=Confidence(grade="medium", depends_on=CONFIDENCE_AVAILABLE),
            limitations=tuple(dict.fromkeys(limitations)),
            metrics=metrics,
        )
