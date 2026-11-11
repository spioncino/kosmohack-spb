"""Часы окна по уровням и тексты причин исключения: общие для выбора окна (`compare`) и объяснения (`explain`)."""

import re

from evarisk.risk.lines.sep import FLUX_NONE, FLUX_OUTLOOK
from evarisk.types import Interval, Window, WindowScore

ILLUMINATION_PREFIX = "ограничение освещённости"
ILLUMINATION_NO_DATA = f"{ILLUMINATION_PREFIX}: данных об освещённости в окне нет"
ILLUMINATION_SHADOW_START = f"{ILLUMINATION_PREFIX}: старт в тени"
CRITICAL_REASON = re.compile(r"^critical по линии (\S+): ([\d.]+) ч$")
MISSING_REASON = re.compile(r"^недостаточно данных по линии (\S+) \((\w+)\)$")
GAP_REASON = re.compile(r"^пробел данных по линии (\S+): (\d+) мин без уровня \(D70\)$")
SUNLIT_REASON = re.compile(rf"^{ILLUMINATION_PREFIX}: доля света ([\d.]+) < ([\d.]+)$")
BEFORE_CUTOFF_REASON = "окно начинается раньше момента отсечения"
# Отметка интервала линии протонов о неизмеренном потоке (D64): «поток: нет отсчёта» или «поток: внешний прогноз
# вероятности N %». На таких минутах уровень взят из прогноза, и ноль опасных минут это не измеренный ноль (D84).
UNMEASURED_FLUX_NOTES = (f"поток: {FLUX_NONE}", "поток: " + FLUX_OUTLOOK.split("{")[0])


def exposure_hours(s: WindowScore) -> float:
    """Часы high и critical по всем линиям штрафа: минуты, по которым действует аналитик."""
    return sum(hours["high"] + hours["critical"] for hours in s.per_line.values())


def elevated_hours(s: WindowScore) -> float:
    """Часы elevated по всем линиям штрафа: вклад в штраф с весом weight_elevated."""
    return sum(hours["elevated"] for hours in s.per_line.values())


def unknown_hours(s: WindowScore) -> float:
    """Часы без уровня по всем линиям штрафа: окно с ними хуже окна без них при равных минутах high+critical."""
    return sum(hours["unknown"] for hours in s.per_line.values())


def unmeasured_flux_minutes(window: Window, intervals: tuple[Interval, ...]) -> int:
    """Минуты окна, где поток протонов не измерен и уровень взят из прогноза, по отметкам интервалов линии протонов."""
    seconds = 0.0
    for iv in intervals:
        if any(mark in iv.note for mark in UNMEASURED_FLUX_NOTES):
            lo, hi = max(window.start, iv.start), min(window.end, iv.end)
            seconds += max(0.0, (hi - lo).total_seconds())
    return round(seconds / 60.0)


def unmeasured_state(minutes: int, window: Window) -> str:
    """«all», когда поток не измерен всё окно, «some», когда часть окна, и пустая строка, когда измерен везде."""
    if minutes <= 0:
        return ""
    return "all" if minutes >= round((window.end - window.start).total_seconds() / 60.0) else "some"
