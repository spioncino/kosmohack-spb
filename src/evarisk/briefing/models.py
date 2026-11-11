from datetime import datetime
from typing import Literal

from pydantic import Field

from evarisk.api.schemas import Mode
from evarisk.types import Availability, Frozen, Level, Provenance

Question = Literal["summary", "can_go_now", "when_better", "what_blocks"]
QUESTIONS: tuple[Question, ...] = ("summary", "can_go_now", "when_better", "what_blocks")
Language = Literal["ru"]
LANGUAGES: tuple[Language, ...] = ("ru",)
Verdict = Literal["keep_requested", "move_to", "insufficient"]
TextSource = Literal["llm", "template"]
Grade = Literal["low", "medium", "high"]
ExclusionKind = Literal["critical", "data_gap", "no_data", "light", "shadow_start", "no_light_data", "other"]


class FactWindow(Frozen):
    """Окно сводки: границы, длительность и минуты, посчитанные по `WindowScore` результата.

    Минуты `None`, когда в штрафе нет ни одной линии: отсутствие данных не превращается в ноль.
    `exposure_minutes`, `unknown_minutes` и `elevated_minutes` это суммы по линиям штрафа (линия-минуты): линии идут
    одновременно, и сумма бывает больше длины окна. В текст они не идут (D84): текст называет минуты по линиям из
    `*_by_line`, где каждое число не больше окна. Суммы остаются для сравнения окон и для страницы.
    """

    start: datetime
    end: datetime
    duration_hours: float
    exposure_minutes: float | None
    unknown_minutes: float | None
    # Минуты уровня elevated: обстановка без экспозиции, но с весом в штрафе окна; у сводок до этого поля None.
    elevated_minutes: float | None = None
    # Линии штрафа с минутами elevated в окне: по ним сводка объясняет, что значит повышенный фон (D77).
    elevated_lines: tuple[str, ...] = ()
    # Минуты по каждой линии штрафа: high и critical, без уровня, elevated (D84). У сводок до D84 пусто.
    exposure_by_line: dict[str, float] = Field(default_factory=dict)
    unknown_by_line: dict[str, float] = Field(default_factory=dict)
    elevated_by_line: dict[str, float] = Field(default_factory=dict)
    # Минуты окна, где поток протонов не измерен и уровень взят из прогноза (D64, D84); у сводок до D84 None.
    flux_unmeasured_minutes: float | None = None


class ExposedInterval(Frozen):
    """Отрезок уровня high или critical внутри запрошенного окна, обрезанный его границами."""

    start: datetime
    end: datetime
    line: str
    level: Level
    minutes: float


class Exclusion(Frozen):
    """Причина исключения окон одного вида по одной линии и число окон с ней (D77)."""

    kind: ExclusionKind
    line: str | None
    windows: int


class DataGap(Frozen):
    line: str
    availability: Availability


class BriefingFacts(Frozen):
    """Факты сводки, собранные из сохранённого расчёта без участия модели.

    `allowed_values` это полный список чисел, времён и дат, которые вправе появиться в тексте; проверка
    текста модели сверяется с ним.
    """

    assessment_id: str
    question: Question
    mode: Mode
    data_moment: datetime | None
    cutoff: datetime | None
    basis: str
    verdict: Verdict
    requested: FactWindow
    recommended: FactWindow | None
    exposed: tuple[ExposedInterval, ...]
    exposed_total: int
    clear_after: datetime | None
    gaps: tuple[DataGap, ...]
    disabled_sources: tuple[str, ...]
    confidence_grade: Grade | None
    confidence_depends_on: str | None
    marginal_note: str | None
    windows_total: int
    windows_excluded: int
    provenance: tuple[Provenance, ...]
    algorithm_version: str
    allowed_values: tuple[str, ...]
    # Причины исключения окон периода и запрошенного окна, по убыванию числа окон; у сводок до D77 пусто.
    exclusions: tuple[Exclusion, ...] = ()
    requested_exclusions: tuple[Exclusion, ...] = ()
    # Минуты периода поиска, на которых поток протонов не измерен (метрика линии протонов), и длина периода поиска
    # в минутах: неизмеренный поток называется в сводке как пробел данных (D78). У сводок до D78 None.
    unmeasured_minutes: float | None = None
    search_minutes: float | None = None


class Briefing(Frozen):
    """Ответ сводки: текст, речевой вариант, происхождение текста и факты, по которым он собран."""

    assessment_id: str
    question: Question
    language: Language
    text: str
    speech_text: str
    source: TextSource
    model: str | None
    validated: bool
    violations: tuple[str, ...]
    facts: BriefingFacts
    generated_at: datetime
    algorithm_version: str
    audio_url: str | None
    voice_error: str | None = None
    asked: str | None = None
    # Версия речевого стиля текста; кеш со старой версией собирается заново (D77). У сводок до D77 это 1.
    style: int = 1
