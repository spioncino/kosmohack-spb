import math

from evarisk.orbit.trajectory import Trajectory
from evarisk.risk.config import SepConfig
from evarisk.risk.lines.sep import cutoff_latitude_deg, grid_step, rigidity_gv
from evarisk.store.asof import AsOf
from evarisk.types import Confidence, Interval, Level, LineAssessment, Window

LIMIT_NOT_A_FORECAST = (
    "геометрия доступа это не прогноз события: минуты, на которых трасса была бы открыта протонам ≥{mev:g} MeV "
    "при Kp {kp:g}, наибольшем снижении широты обрезания; при более слабой буре таких минут меньше"
)
LIMIT_INFORMATIONAL = "линия геометрии доступа информационная: в сравнении окон не участвует (D61)"
LIMIT_VERTICAL = "жёсткость обрезания вертикальная, зависимость широты обрезания от Kp линейная (D36)"
NOTE_CLOSED = "трасса закрыта для протонов ≥{mev:g} MeV даже при Kp {kp:g}"
NOTE_OPEN = (
    "проход открывается с Kp {kp_open:.1f}; пик магнитной широты {peak:.1f}°, широта обрезания при Kp {kp:g} "
    "{cutoff:.1f}°"
)
CONFIDENCE_VERIFIED = "траектория и модель обрезания; коэффициент поправки по Kp сверен"
CONFIDENCE_UNVERIFIED = "коэффициент поправки по Kp оценён по рисунку статьи, не сверен (D36)"
_MINUTES_PER_HOUR = 60.0


class AccessGeometryLine:
    """Страховка по геометрии (D61): минуты, на которых трасса открыта протонам при наибольшем Kp.

    Считается по траектории и модели обрезания без данных о космической погоде: `elevated` на минутах возможного
    доступа, `none` на закрытых. Линия информационная, в сравнении окон не участвует.
    """

    name = "proton_access_geometry"
    mechanism = "radiation_access_geometry"

    def __init__(self, cfg: SepConfig) -> None:
        self.cfg = cfg
        self._rigidity = rigidity_gv(cfg.suit_threshold_mev, cfg.proton_rest_energy_mev)
        self._kp = cfg.kp_assumed_when_missing
        self._quiet = cutoff_latitude_deg(self._rigidity, cfg)
        self._cutoff = cutoff_latitude_deg(self._rigidity, cfg, kp=self._kp)

    def _rule(self) -> str:
        return (
            f"геомагнитный доступ протонов ≥{self.cfg.suit_threshold_mev:g} MeV: |mlat| не ниже широты обрезания "
            f"arccos(((R+{self.cfg.cutoff_offset_gv:g})/{self.cfg.cutoff_constant_gv:g})^0.25) минус "
            f"{self.cfg.kp_suppression_deg_per_kp:g}°·Kp "
            f"при Kp {self._kp:g}, то есть {self._cutoff:.1f}° (D36, D61)"
        )

    def _state(self, mlat: float) -> Level:
        # AACGM не определён только вблизи магнитного экватора (orbit/magnetic.py): это низкие широты, трасса закрыта.
        if math.isnan(mlat):
            return "none"
        return "elevated" if abs(mlat) >= self._cutoff else "none"

    def _note(self, level: Level, peak: float) -> str:
        if level == "none":
            return NOTE_CLOSED.format(mev=self.cfg.suit_threshold_mev, kp=self._kp)
        per_kp = self.cfg.kp_suppression_deg_per_kp or math.nan
        kp_open = max(0.0, (self._quiet - peak) / per_kp)
        return NOTE_OPEN.format(kp_open=kp_open, peak=peak, kp=self._kp, cutoff=self._cutoff)

    def assess(self, window: Window, trajectory: Trajectory, asof: AsOf) -> LineAssessment:
        in_window = [i for i, t in enumerate(trajectory.times) if window.start <= t < window.end]
        if not in_window:
            raise ValueError("траектория не покрывает окно")
        step = grid_step(trajectory.times)
        states = [self._state(float(trajectory.mlat_deg[i])) for i in in_window]
        intervals: list[Interval] = []
        run_start = 0
        for j in range(1, len(in_window) + 1):
            if j == len(in_window) or states[j] != states[run_start]:
                level = states[run_start]
                peaks = (
                    [abs(float(trajectory.mlat_deg[i])) for i in in_window[run_start:j]] if level == "elevated" else []
                )
                intervals.append(
                    Interval(
                        start=trajectory.times[in_window[run_start]],
                        end=min(trajectory.times[in_window[j - 1]] + step, window.end),
                        level=level,
                        kind="computed",
                        provenance=(trajectory.provenance,),
                        rule=self._rule(),
                        note=self._note(level, max(peaks) if peaks else math.nan),
                    )
                )
                run_start = j
        step_minutes = step.total_seconds() / _MINUTES_PER_HOUR
        known = [abs(v) for v in (float(trajectory.mlat_deg[i]) for i in in_window) if not math.isnan(v)]
        metrics: dict[str, float] = {
            "access_minutes": states.count("elevated") * step_minutes,
            "kp": self._kp,
            "cutoff_deg": self._cutoff,
        }
        if known:
            metrics["max_abs_mlat_deg"] = max(known)
        confidence = Confidence(
            grade="high" if self.cfg.kp_suppression_verified else "medium",
            depends_on=CONFIDENCE_VERIFIED if self.cfg.kp_suppression_verified else CONFIDENCE_UNVERIFIED,
        )
        return LineAssessment(
            line=self.name,
            mechanism=self.mechanism,
            intervals=tuple(intervals),
            availability="available" if known else "missing",
            confidence=confidence,
            limitations=(
                LIMIT_NOT_A_FORECAST.format(mev=self.cfg.suit_threshold_mev, kp=self._kp),
                LIMIT_INFORMATIONAL,
                LIMIT_VERTICAL,
                *trajectory.limitations,
            ),
            metrics=metrics,
        )
