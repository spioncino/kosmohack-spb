from evarisk.orbit.trajectory import Trajectory
from evarisk.risk.lines.sep import grid_step
from evarisk.store.asof import AsOf
from evarisk.types import Confidence, Interval, Level, LineAssessment, Window

RULE = "тень по цилиндрической модели Земли, направление на Солнце по USNO (см. orbit/sun.py)"
LIMIT_CONDITION = (
    "освещённость это условие работ, не опасность; в штрафе окон не участвует, "
    "применяется только как явно заданное ограничение"
)
CONFIDENCE_RECONSTRUCTION = (
    "цилиндрическая тень без полутени, направление на Солнце по USNO (около 0.01°); геометрия по OEM, реконструкция"
)
CONFIDENCE_PREDICTION = (
    "цилиндрическая тень без полутени, направление на Солнце по USNO (около 0.01°); "
    "геометрия по OEM, прогноз на момент данных"
)
NOTE_SUNLIT = "свет"
NOTE_SHADOW = "тень"
_MINUTES_PER_HOUR = 60.0


class IlluminationLine:
    """Свет и тень на траектории: `none` на свету, `elevated` в тени, только для отображения и ограничений."""

    name = "illumination"
    mechanism = "work_conditions"

    def assess(self, window: Window, trajectory: Trajectory, asof: AsOf) -> LineAssessment:
        in_window = [i for i, t in enumerate(trajectory.times) if window.start <= t < window.end]
        if not in_window:
            raise ValueError("траектория не покрывает окно")
        sunlit = [bool(trajectory.sunlit[i]) for i in in_window]
        step = grid_step(trajectory.times)
        step_minutes = step.total_seconds() / _MINUTES_PER_HOUR
        intervals: list[Interval] = []
        longest_shadow = 0
        run_start = 0
        for j in range(1, len(in_window) + 1):
            if j == len(in_window) or sunlit[j] != sunlit[run_start]:
                lit = sunlit[run_start]
                level: Level = "none" if lit else "elevated"
                if not lit:
                    longest_shadow = max(longest_shadow, j - run_start)
                intervals.append(
                    Interval(
                        start=trajectory.times[in_window[run_start]],
                        end=min(trajectory.times[in_window[j - 1]] + step, window.end),
                        level=level,
                        kind="computed",
                        provenance=(trajectory.provenance,),
                        rule=RULE,
                        note=NOTE_SUNLIT if lit else NOTE_SHADOW,
                    )
                )
                run_start = j
        metrics: dict[str, float] = {
            "sunlit_fraction": sum(sunlit) / len(sunlit),
            "longest_shadow_minutes": longest_shadow * step_minutes,
            "starts_in_daylight": 1.0 if sunlit[0] else 0.0,
        }
        confidence = (
            Confidence(grade="high", depends_on=CONFIDENCE_RECONSTRUCTION)
            if trajectory.is_reconstruction
            else Confidence(grade="medium", depends_on=CONFIDENCE_PREDICTION)
        )
        return LineAssessment(
            line=self.name,
            mechanism=self.mechanism,
            intervals=tuple(intervals),
            availability="available",
            confidence=confidence,
            limitations=(LIMIT_CONDITION,),
            metrics=metrics,
        )
