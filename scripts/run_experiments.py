"""Эксперимент P2-6 (Т5, R25): replay на событиях 10–12 мая и 8 июня 2024 и на контрольном периоде против бейзлайнов.

Запуск: EVARISK_RAW_ROOT=<хранилище> EVARISK_ASSESSMENTS_ROOT=<каталог> uv run python scripts/run_experiments.py
Результаты: docs/experiments/<период>_<отсечение>.json и сводка docs/experiments/README.md.
"""

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import numpy as np

from evarisk.api.schemas import AssessmentRequest, AssessmentResult
from evarisk.api.service import run_assessment
from evarisk.config import Settings
from evarisk.parsers.sep_list import SepEvent, parse_sep_page
from evarisk.replay.baseline import Baseline, persistence, single_alert, swpc_outlook
from evarisk.replay.verify import WARNING_LEVELS, Verification, compare
from evarisk.risk.config import load_sep_config
from evarisk.store.asof import AsOf
from evarisk.store.raw import RawStore
from evarisk.store.views import kp_series, proton_series
from evarisk.types import Interval, Level, Window

DURATION_HOURS = 6.0
SEARCH_START_HOUR = 0
# Период поиска это сутки: ТЗ задаёт поиск окна на 24 часа.
SEARCH_HOURS = 24
CONTROL_KP_MAX = 3.0
CONTROL_FROM = date(2024, 6, 13)
CONTROL_TO = date(2024, 6, 30)
CONTROL_CUTOFF_TIME = time(0, 30)
LEVEL_ORDER: tuple[Level, ...] = ("none", "elevated", "high", "critical", "unknown")
BASELINES = ("persistence", "single_alert", "swpc_outlook")
OTHER_LINE = "meteor_showers"

INTRO = """# Эксперименты: replay против факта и бейзлайнов

Сгенерировано `scripts/run_experiments.py` на сыром хранилище зеркала 1 марта – 2 июля 2024 (D33).
Версия алгоритма: {versions}. Каждый JSON содержит полный `AssessmentResult` с полем `verification`,
три бейзлайна и их сверку той же функцией.

## Что сравнивается

- Наш прогноз: `POST /assessments` в режиме `replay` с отсечением T; линия SEP читает только опубликованное
  к T (D39): Kp «как знали тогда», прогноз Kp SWPC, персистентность потока не дальше 6 ч, вероятность
  протонного события daypre/3-day, а также уведомления DONKI (D44): пересечения порогов GOES (≥10 MeV
  > 10 pfu, ≥100 MeV > 1 pfu) задают уровень не ниже `elevated`, `high` или `critical` на минутах доступа; событие
  считается идущим 48 ч и 24 ч после последнего подтверждения, пока три измеренных отсчёта не покажут поток
  ниже порога (D60, D64); прогнозы прихода CME поднимают Kp после T до своего максимума. Широта обрезания по
  фиту измерений SAMPEX, 63.4° для 30 MeV (D62). Окно ВКД 6 ч, период поиска сутки события 00:00–24:00 UTC,
  шаг сетки окон 15 мин.
- Факт: тот же расчёт линии SEP и окон с `AsOf(None)` (GFZ definitive, переобработанный ряд GOES,
  эфемерида по доступности); хранится в `verification` и на рекомендацию не влияет (D5, D41).
- Метрики (D41, D63, D65): по минутам периода поиска с порогом ≥ high. Уровни `high` и `critical` ставятся
  только на минутах геомагнитного доступа, поэтому это минуты экспозиции экипажа: попадания, пропуски,
  ложные тревоги; минуты без факта считаются непроверяемыми. Заблаговременность это минуты от отсечения
  до первого факта ≥ high при предупреждении, выданном не позже него; она показывает, за сколько сервис
  предупредил, а не точность времени события. Выбор окна устоял, если окно прогноза совпало с окном по
  факту или входит в равнозначные ему по факту.
- Бейзлайн «персистентность» (последнее наблюдение): уровень по шкале S последнего доступного на момент T
  отсчёта p10 распространяется на весь период. Бейзлайн «единственное предупреждение»: последнее на
  момент T утверждение о шкале S (строка 3-day forecast «above/below S-scale storm level thresholds» или
  сообщение DONKI SEP о превышении 10 pfu по ≥10 MeV на GOES) даёт `elevated` всему периоду при S ≥ 1,
  иначе `none`. Бейзлайн «прогноз SWPC без орбиты» (D66): вероятность протонного события S1+ из последнего на
  момент T выпуска daypre или 3-day forecast на каждые сутки, не ниже 50 % даёт `elevated`, ниже `none`; это то
  же основание, что у нашей линии за горизонтом персистентности, но без минут доступа. Окна у бейзлайнов
  ранжируются теми же правилами `config/windows.toml`, сверка той же функцией `replay.verify.compare`.
- Главный показатель для оператора: минуты экспозиции по факту в окне, которое выбрал подход. Он не зависит от
  того, как бейзлайн переводит свой прогноз в уровни, и считается сервисом в каждом прогнозе из прошлого (D66).

## Периоды и обоснование дат
"""

CONTROL_CRITERION = """
Критерий контрольных суток: первые сутки в июне 2024 после 12 июня с максимумом Kp GFZ definitive
≤ {kp_limit:g}, без события с началом в этих сутках в официальном списке NOAA и с наблюдённым по GOES p10
ниже порога S1 весь день (официальный список не содержит времени окончания события, поэтому хвост события
8 июня отсекается по наблюдению). Выбраны сутки {day}: максимум Kp {kp_max:.2f}, максимум p10 {p10:.1f} pfu.
{rejected}
"""

TABLE_NOTE = """
Минуты по уровням даны на периоде поиска (1440 мин); «н/о» это не определена, причина в поле `note` JSON.
Столбец «≥ elevated» это минуты с предупреждением любого уровня у прогноза и у факта; попадания, пропуски
и ложные тревоги считаются по порогу ≥ high (D41). Бейзлайн заменяет только линию SEP одним уровнем на
весь период; линия метеоров из того же replay-расчёта участвует в ранжировании окон у всех четырёх подходов,
поэтому при одинаковом уровне SEP на всех окнах выбор окна у бейзлайна определяется метеорной линией. При
`unknown` оснований для выбора нет.
"""

LIMITS = """
## Как читать факт

- Уровень линии протонов в точке (D63): без геомагнитного доступа обстановка по шкале S даёт не выше
  `elevated`; при доступе `critical` при потоке ≥100 MeV не ниже 1 pfu, `high` при потоке ≥30 MeV не ниже
  1 pfu или при обстановке S2 без измеренного спектра. Поэтому факт `high` и `critical` это минуты, когда
  протоны доставали станцию: 11 мая при Kp 8–9 это витки с 02:18 до 09:29 и виток 23:09–23:16 (107 мин),
  8 июня при спокойном Kp только четыре поздних витка 14:32–19:21 (32 мин), хотя поток ≥10 MeV держался
  выше 100 pfu большую часть суток.
- Окно по факту выбирается теми же правилами: окно с `critical` исключается, первым сравниваются минуты
  `high` и `critical`, затем штраф за `elevated`; при равнозначных окнах берётся ближайшее к запрошенному.

## Ограничение источников, не модели

- Суточные файлы GOES SGPS появляются в архиве NCEI через 6–7 суток; доступность файла это позднее из
  «следующие сутки 00:05 UTC» и HTTP Last-Modified (D69). При отсечении 10–11 мая 2024 последний доступный файл
  за 3 мая: измерений потока за неделю до отсечения нет, персистентность до окна не дотягивает, уровень задаётся
  внешним прогнозом вероятности и сообщениями DONKI. Рекомендация прямо говорит, на скольких минутах поток не
  измерен (D64).
  Операционный поток SWPC в реальном времени был, но его архив за 2024 год не публикуется (D19).
- Архив 3-day forecast NCEI за май 2024 неполный (28 файлов из 62), за 1–15 июня 2024 выпусков нет вовсе;
  geomag_forecast за 1–15 июня тоже отсутствует. Для отсечений 7 и 8 июня прогноз Kp SWPC недоступен,
  доступ считается консервативно по Kp 9 (D54), вероятность протонного события берётся из daypre.
- Бейзлайн «единственное предупреждение» опирается на последнее доступное утверждение о шкале S. Из-за
  пробела в архиве 3-day forecast для отсечений 7 и 8 июня последним выпуском оказывается 14 мая 12:30
  («above», давность 24 сут), а для контрольных суток последним утверждением остаётся сообщение DONKI SEP
  от 8 июня 03:07 без отбоя; оба дают `elevated` без связи с обстановкой на дату окна.
- Ложные тревоги и пропуски считаются честно: минуты факта `unknown` не засчитываются ни в одну сторону.
"""

SUMMARY = """
Общее (текст сверен с прогоном 2026-09-19 после правил D60–D65; числа в таблице выше).

- Событие 10–12 мая, отсечения 10 мая 00:30, 12:30 и 16:00: измерений потока на 11 мая нет ни одного,
  рекомендация так и говорит. Минуты доступа известны заранее по траектории и прогнозу Kp; при вероятности
  SWPC 50–55 % и событии DONKI ≥10 MeV с 10 мая 13:35, которое удерживается 48 ч (D60), они получают `high`
  как условная экспозиция (D47). Факт ≥ high 107 мин; прогноз назвал 93 из них. Пропуски 14 мин: витки
  02:18–02:22 и 23:09–23:16, закрытые при прогнозном Kp с запасом 0.4° и 0.6°, меньше одной единицы Kp, и
  поэтому названные в ответе пограничными (D49), плюс края проходов по одной минуте. Ложные тревоги 18 мин:
  витки 00:00–00:06 и 01:29–01:41 открыты и в факте, но событие ≥100 MeV началось только в 02:10.
  Заблаговременность 1548, 828 и 618 мин до первого факта 02:18. Бейзлайны не дают ни одного попадания,
  и у двух из них это по построению: «единственное предупреждение» и «прогноз SWPC без орбиты» выдают только
  `none` или `elevated` и не могут дать `high`; «персистентность» может, но видит отсчёт GOES 6.5–7 суток
  давности (файлы NCEI выходят с задержкой). У всех один уровень на весь период, они не различают минуты
  внутри суток. «Прогноз SWPC без орбиты»
  предупреждает так же рано, как наш расчёт, но оставляет запрошенное окно 04:00–10:00, где по факту 74 мин
  экспозиции: одно число на сутки не выбирает окно.
- Выбор окна на тех же отсечениях: запрошенное 04:00–10:00 содержит 72 мин экспозиции, после витка
  09:20–09:29 следующий открытый виток только в 23:09: пики магнитной широты по виткам падают с 63–66° утром
  до 55° и ниже днём, это суточный цикл трассы относительно магнитного полюса. По D49 перенос идёт на
  ближайшее к запрошенному окно без экспозиции: 09:30–15:30, оно же окно по факту.
- Отсечение 9 мая 00:30: вероятность 25 % ниже порога и событий DONKI ещё нет, уровень `none` по внешнему
  прогнозу, все 107 мин факта пропущены. Этого события на этот момент не видел никто: пропуск у бейзлайнов
  тот же.
- Отсечение 11 мая 03:00 (после сообщений DONKI 02:30 и 02:46 о потоке ≥100 MeV выше 1 pfu с 02:10):
  `critical` на минутах доступа, 90 попаданий, 17 пропусков, 6 ложных тревог. Первый факт 02:18 раньше
  отсечения, поэтому заблаговременность не определена. Окно 09:30–15:30, как и по факту.
- Событие 8 июня: сообщения DONKI (02:36 о ≥100 MeV с 02:25, 03:04 о ≥10 MeV с 02:55) выходят после обоих
  отсечений (7 июня 12:30, 8 июня 00:30), выпусков 3-day forecast за 1–15 июня в архиве нет, поэтому `none`
  по вероятности daypre 10 % с низкой уверенностью. Событие вызвала вспышка M9.7 8 июня 01:49, которой не
  было в прогнозе, и 32 мин экспозиции на витках 14:32–19:21 пропущены. Оставленное окно 06:00–12:00
  экспозиции по факту не содержит; окно по факту 01:00–07:00 при отсечении 7 июня 12:30 и 01:30–07:30
  при отсечении 8 июня 00:30, в нём меньше минут обстановки `elevated`.
- Контрольные сутки 13 июня: факт `none`, наш прогноз `none`, ложных тревог нет. Сообщение DONKI от 8 июня
  без отбоя к этим суткам вышло за срок удержания и нижней границы уровня (D60, D64); «единственное
  предупреждение» по тому же сообщению даёт `elevated`.
- Итог: за сутки до события, без единого измерения потока, расчёт по трассе называет 93 из 107 мин
  экспозиции и выбирает окно, совпавшее с фактом, потому что использует то, что известно заранее:
  геометрию доступа и прогноз Kp. Цена 18 мин ложных тревог на витках, открывшихся раньше начала события.
  Слабость общая с бейзлайнами и вызвана источником: событие 8 июня и 9 мая 00:30 ниже порога прогноза SWPC
  не видит никто.
"""


@dataclass(frozen=True)
class Period:
    name: str
    title: str
    basis: str
    day: date
    eva_hour: int
    cutoffs: tuple[datetime, ...]

    def request(self, cutoff: datetime) -> AssessmentRequest:
        return AssessmentRequest(
            mode="replay",
            cutoff=cutoff,
            eva_start=datetime.combine(self.day, time(self.eva_hour), tzinfo=UTC),
            duration_hours=DURATION_HOURS,
            search_start=datetime.combine(self.day, time(SEARCH_START_HOUR), tzinfo=UTC),
            search_end=datetime.combine(self.day, time(SEARCH_START_HOUR), tzinfo=UTC) + timedelta(hours=SEARCH_HOURS),
        )


@dataclass(frozen=True)
class BaselineRun:
    baseline: Baseline
    verification: Verification


@dataclass(frozen=True)
class Run:
    period: Period
    cutoff: datetime
    result: AssessmentResult
    baselines: dict[str, BaselineRun]

    @property
    def verification(self) -> Verification:
        if self.result.verification is None:
            raise ValueError(f"результат {self.result.id} без сверки")
        return self.result.verification

    @property
    def span(self) -> Window:
        return Window(start=self.result.request.search_start, end=self.result.request.search_end)


@dataclass(frozen=True)
class ControlChoice:
    day: date
    kp_max: float
    p10_max_pfu: float
    rejected: tuple[str, ...]


EVENT_PERIODS = (
    Period(
        name="event_2024-05-11",
        title="событие 10–12 мая 2024",
        basis="официальный список NOAA: 10 мая 13:35–17:45 UTC, 208 pfu; 11 мая 02:10–09:10 UTC, 116 pfu (D25)",
        day=date(2024, 5, 11),
        eva_hour=4,
        # Отсечения 16:00 и 03:00 позже уведомлений DONKI о пересечении порогов (13:46 и 02:30, D44).
        cutoffs=(
            datetime(2024, 5, 9, 0, 30, tzinfo=UTC),
            datetime(2024, 5, 10, 0, 30, tzinfo=UTC),
            datetime(2024, 5, 10, 12, 30, tzinfo=UTC),
            datetime(2024, 5, 10, 16, 0, tzinfo=UTC),
            datetime(2024, 5, 11, 3, 0, tzinfo=UTC),
        ),
    ),
    Period(
        name="event_2024-06-08",
        title="событие 8 июня 2024",
        basis="официальный список NOAA: 8 июня 02:55–08:00 UTC, 1030 pfu (D25)",
        day=date(2024, 6, 8),
        eva_hour=6,
        cutoffs=(datetime(2024, 6, 7, 12, 30, tzinfo=UTC), datetime(2024, 6, 8, 0, 30, tzinfo=UTC)),
    ),
)


def _official_events(asof: AsOf) -> list[SepEvent]:
    rec = asof.latest("swpc_spe", "sep_list")
    if rec is None:
        raise SystemExit("в хранилище нет страницы официального списка протонных событий (swpc_spe/sep_list)")
    return parse_sep_page(asof.read(rec).decode("utf-8", errors="replace"))


def _day_check(asof: AsOf, day: date, events: list[SepEvent], s1: float) -> tuple[str | None, float, float]:
    start = datetime.combine(day, time(0), tzinfo=UTC)
    end = start + timedelta(days=1)
    kp = kp_series(asof, start, end)
    if kp is None:
        return "нет Kp GFZ", float("nan"), float("nan")
    kp_max = max(float(k) for s, k in zip(kp.starts, kp.kp, strict=True) if start <= s < end)
    protons = proton_series(asof, start, end)
    finite = protons.p10[~np.isnan(protons.p10)] if protons is not None else np.asarray([])
    if finite.size == 0:
        return "нет отсчётов GOES", kp_max, float("nan")
    p10_max = float(np.max(finite))
    begun = [e for e in events if start <= e.begin < end]
    if kp_max > CONTROL_KP_MAX:
        return f"максимум Kp {kp_max:.2f} > {CONTROL_KP_MAX:g}", kp_max, p10_max
    if begun:
        return f"в официальном списке событие с началом {begun[0].begin:%Y-%m-%d %H:%M}", kp_max, p10_max
    if p10_max >= s1:
        return f"наблюдённый p10 до {p10_max:.1f} pfu не ниже S1 {s1:g}", kp_max, p10_max
    return None, kp_max, p10_max


def select_control_day(store: RawStore) -> ControlChoice:
    asof = AsOf(store, None)
    events = _official_events(asof)
    s1 = load_sep_config().s1_pfu
    rejected: list[str] = []
    day = CONTROL_FROM
    while day <= CONTROL_TO:
        reason, kp_max, p10_max = _day_check(asof, day, events, s1)
        if reason is None:
            return ControlChoice(day=day, kp_max=kp_max, p10_max_pfu=p10_max, rejected=tuple(rejected))
        rejected.append(f"{day}: {reason}")
        day += timedelta(days=1)
    raise SystemExit("контрольные сутки не найдены: " + "; ".join(rejected))


def control_period(choice: ControlChoice) -> Period:
    cutoff = datetime.combine(choice.day - timedelta(days=1), CONTROL_CUTOFF_TIME, tzinfo=UTC)
    basis = (
        f"первые сутки после 12 июня 2024 с максимумом Kp GFZ definitive {choice.kp_max:.2f} ≤ {CONTROL_KP_MAX:g}, "
        f"без события в официальном списке NOAA, наблюдённый p10 GOES не выше {choice.p10_max_pfu:.1f} pfu (ниже S1)"
    )
    return Period(
        name=f"control_{choice.day.isoformat()}",
        title=f"контрольный период {choice.day:%d.%m.%Y}",
        basis=basis,
        day=choice.day,
        eva_hour=4,
        cutoffs=(cutoff,),
    )


def _baseline_run(b: Baseline, req: AssessmentRequest, v: Verification) -> BaselineRun:
    if req.cutoff is None:
        raise ValueError("бейзлайн требует cutoff")
    verification = compare(
        req.cutoff,
        b.line.intervals,
        v.observed,
        Window(start=req.search_start, end=req.search_end),
        predicted_preferred=b.recommendation.preferred,
        observed_preferred=v.observed_preferred,
        observed_insufficient=v.observed_insufficient,
        observed_availability=v.observed_availability,
        notes=(f"бейзлайн {b.name}: {b.basis}",),
    )
    return BaselineRun(baseline=b, verification=verification)


def run_one(period: Period, cutoff: datetime, store: RawStore, settings: Settings) -> Run:
    req = period.request(cutoff)
    result = run_assessment(req, store, settings)
    if result.verification is None:
        raise SystemExit(f"результат {result.id} без сверки")
    # Бейзлайн заменяет только линию SEP: остальные линии того же replay-расчёта участвуют в ранжировании окон.
    others = tuple(a for a in result.lines if a.line == OTHER_LINE)
    computed = (
        persistence(req, store, other_lines=others),
        single_alert(req, store, other_lines=others),
        swpc_outlook(req, store, other_lines=others),
    )
    baselines = {b.name: _baseline_run(b, req, result.verification) for b in computed}
    return Run(period=period, cutoff=cutoff, result=result, baselines=baselines)


def _warning_minutes(intervals: tuple[Interval, ...], span: Window) -> float:
    total = 0.0
    for iv in intervals:
        lo, hi = max(iv.start, span.start), min(iv.end, span.end)
        if hi > lo and iv.level in WARNING_LEVELS:
            total += (hi - lo).total_seconds() / 60.0
    return total


def _level_minutes(intervals: tuple[Interval, ...], span: Window) -> str:
    counter: Counter[str] = Counter()
    for iv in intervals:
        lo, hi = max(iv.start, span.start), min(iv.end, span.end)
        if hi > lo:
            counter[iv.level] += int((hi - lo).total_seconds() // 60)
    total = int(span.duration.total_seconds() // 60)
    covered = sum(counter.values())
    if covered < total:
        counter["unknown"] += total - covered
    return ", ".join(f"{level} {counter[level]}" for level in LEVEL_ORDER if counter[level])


def _window_text(window: Window | None) -> str:
    return "оснований нет" if window is None else f"{window.start:%H:%M}–{window.end:%H:%M}"


def _minutes(value: float | None) -> str:
    return "н/о" if value is None else f"{value:.0f}"


def _stamp(moment: datetime | None) -> str:
    return "—" if moment is None else f"{moment:%Y-%m-%d %H:%M}"


def _yes(flag: bool) -> str:
    return "да" if flag else "нет"


def _forecast_basis(run: Run) -> str:
    sep = next(a for a in run.result.lines if a.line == "sep_protons")
    notes = list(dict.fromkeys(iv.note for iv in sep.intervals if iv.note))
    outlook = [lim for lim in sep.limitations if "вероятност" in lim or "DONKI" in lim]
    return "; ".join([*notes, *outlook]) if notes or outlook else "интервалов нет"


def _exposure_in_window(run: Run, name: str) -> str:
    """Минуты экспозиции по факту в окне бейзлайна из сверки самого сервиса (D66)."""
    check = next((c for c in run.verification.baselines if c.name == name), None)
    return _minutes(None if check is None else check.preferred_exposure_minutes)


def _baseline_cell(b: BaselineRun, span: Window, exposure: str) -> str:
    v = b.verification
    return (
        f"{b.baseline.level} (≥ elevated {_warning_minutes(b.baseline.line.intervals, span):.0f} мин); "
        f"окно {_window_text(b.baseline.recommendation.preferred)}, экспозиция по факту в нём {exposure}; "
        f"попадания {v.hit_minutes:.0f}, пропуски {v.miss_minutes:.0f}, ложные {v.false_alarm_minutes:.0f}; "
        f"устойчиво {_yes(v.window_choice_stable)}"
    )


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _table(runs: list[Run]) -> list[str]:
    head = [
        "период",
        "отсечение, UTC",
        "наш прогноз SEP, мин по уровням",
        "≥ elevated, мин: прогноз / факт",
        "наша рекомендация",
        "экспозиция по факту в нашем окне",
        "заблаговременность, мин",
        "попадания",
        "пропуски",
        "ложные тревоги",
        "без факта",
        "окно устойчиво",
        "персистентность",
        "единственное предупреждение",
        "прогноз SWPC без орбиты",
    ]
    lines = [_row(head), _row(["---"] * len(head))]
    for run in runs:
        v = run.verification
        cells = [
            run.period.title,
            f"{run.cutoff:%Y-%m-%d %H:%M}",
            _level_minutes(v.predicted, run.span),
            f"{_warning_minutes(v.predicted, run.span):.0f} / {_warning_minutes(v.observed, run.span):.0f}",
            _window_text(run.result.recommendation.preferred),
            _minutes(v.preferred_exposure_minutes),
            _minutes(v.lead_time_minutes),
            f"{v.hit_minutes:.0f}",
            f"{v.miss_minutes:.0f}",
            f"{v.false_alarm_minutes:.0f}",
            f"{v.unverified_minutes:.0f}",
            _yes(v.window_choice_stable),
            *(_baseline_cell(run.baselines[name], run.span, _exposure_in_window(run, name)) for name in BASELINES),
        ]
        lines.append(_row(cells))
    return lines


def _fact_table(runs: list[Run]) -> list[str]:
    head = [
        "период",
        "отсечение, UTC",
        "факт SEP, мин по уровням",
        "первый факт ≥ high",
        "окно по факту",
        "данные факта",
        "основание нашего прогноза",
    ]
    lines = [_row(head), _row(["---"] * len(head))]
    for run in runs:
        v = run.verification
        cells = [
            run.period.title,
            f"{run.cutoff:%Y-%m-%d %H:%M}",
            _level_minutes(v.observed, run.span),
            _stamp(v.first_observed_alarm),
            _window_text(v.observed_preferred),
            v.observed_availability,
            _forecast_basis(run),
        ]
        lines.append(_row(cells))
    return lines


def _baseline_basis(runs: list[Run]) -> list[str]:
    head = [
        "период",
        "отсечение, UTC",
        "персистентность: основание",
        "единственное предупреждение: основание",
        "прогноз SWPC без орбиты: основание",
    ]
    lines = [_row(head), _row(["---"] * len(head))]
    for run in runs:
        cells = [
            run.period.title,
            f"{run.cutoff:%Y-%m-%d %H:%M}",
            *(run.baselines[name].baseline.basis for name in BASELINES),
        ]
        lines.append(_row(cells))
    return lines


def _conclusions(runs: list[Run]) -> list[str]:
    out: list[str] = []
    for run in runs:
        v = run.verification
        fact = v.hit_minutes + v.miss_minutes
        ours = _window_text(run.result.recommendation.preferred)
        parts = [
            f"- {run.period.title}, отсечение {run.cutoff:%Y-%m-%d %H:%M}: факт ≥ high {fact:.0f} мин",
            f"наш прогноз: попадания {v.hit_minutes:.0f}, пропуски {v.miss_minutes:.0f}, "
            f"ложные {v.false_alarm_minutes:.0f}, заблаговременность {_minutes(v.lead_time_minutes)}, "
            f"окно {ours} против факта {_window_text(v.observed_preferred)}",
        ]
        for name in BASELINES:
            b = run.baselines[name]
            parts.append(
                f"{name}: пропуски {b.verification.miss_minutes:.0f}, ложные {b.verification.false_alarm_minutes:.0f}, "
                f"окно {_window_text(b.baseline.recommendation.preferred)}"
            )
        out.append("; ".join(parts) + ".")
    return out


def file_name(run: Run) -> str:
    return f"{run.period.name}_{run.cutoff:%Y-%m-%dT%H%MZ}.json"


def _period_lines(periods: tuple[Period, ...]) -> list[str]:
    lines = []
    for period in periods:
        first = period.request(period.cutoffs[0])
        end = first.eva_start + timedelta(hours=DURATION_HOURS)
        cutoffs = ", ".join(f"{c:%Y-%m-%d %H:%M}" for c in period.cutoffs)
        lines.append(
            f"- {period.title}: окно {first.eva_start:%Y-%m-%d %H:%M}–{end:%H:%M} UTC, период поиска "
            f"{first.search_start:%H:%M}–{first.search_end:%H:%M} UTC, отсечения {cutoffs}. Основание: {period.basis}."
        )
    return lines


def readme(runs: list[Run], periods: tuple[Period, ...], control: ControlChoice, versions: set[str]) -> str:
    rejected = ("Отклонённые кандидаты: " + "; ".join(control.rejected) + ".") if control.rejected else ""
    parts = [
        INTRO.format(versions=", ".join(sorted(versions))),
        "\n".join(_period_lines(periods)),
        CONTROL_CRITERION.format(
            kp_limit=CONTROL_KP_MAX,
            day=control.day.isoformat(),
            kp_max=control.kp_max,
            p10=control.p10_max_pfu,
            rejected=rejected,
        ),
        "## Сводная таблица\n\n" + "\n".join(_table(runs)),
        TABLE_NOTE,
        "## Факт и основание прогноза\n\n" + "\n".join(_fact_table(runs)),
        "\n## Основания бейзлайнов\n\n" + "\n".join(_baseline_basis(runs)),
        LIMITS,
        "## Что показывает сравнение\n\n" + "\n".join(_conclusions(runs)),
        SUMMARY,
        "## Файлы\n\n"
        + "\n".join(
            f"- `{file_name(run)}`: {run.period.title}, отсечение {run.cutoff:%Y-%m-%d %H:%M} UTC" for run in runs
        ),
    ]
    return "\n".join(parts) + "\n"


def save_run(run: Run, out_dir: Path) -> Path:
    req = run.result.request
    payload = {
        "period": {
            "name": run.period.name,
            "title": run.period.title,
            "basis": run.period.basis,
            "eva_start": req.eva_start.isoformat().replace("+00:00", "Z"),
            "duration_hours": req.duration_hours,
            "search_start": req.search_start.isoformat().replace("+00:00", "Z"),
            "search_end": req.search_end.isoformat().replace("+00:00", "Z"),
        },
        "cutoff": run.cutoff.isoformat().replace("+00:00", "Z"),
        "result": run.result.model_dump(mode="json"),
        "baselines": {
            name: {
                "baseline": b.baseline.model_dump(mode="json"),
                "verification": b.verification.model_dump(mode="json"),
            }
            for name, b in run.baselines.items()
        },
    }
    path = out_dir / file_name(run)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Эксперимент P2-6: replay против факта и бейзлайнов")
    parser.add_argument("--out", type=Path, default=Path("docs/experiments"), help="каталог результатов")
    args = parser.parse_args()
    settings = Settings()
    store = RawStore(settings.raw_root)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    control = select_control_day(store)
    print(f"контрольные сутки: {control.day} (Kp max {control.kp_max:.2f}, p10 max {control.p10_max_pfu:.1f} pfu)")
    for reason in control.rejected:
        print(f"  отклонено: {reason}")
    periods = (*EVENT_PERIODS, control_period(control))
    runs: list[Run] = []
    for period in periods:
        for cutoff in period.cutoffs:
            run = run_one(period, cutoff, store, settings)
            runs.append(run)
            v = run.verification
            path = save_run(run, out_dir)
            print(
                f"{period.title} @ {cutoff:%Y-%m-%d %H:%M}: окно {_window_text(run.result.recommendation.preferred)}, "
                f"попадания {v.hit_minutes:.0f}, пропуски {v.miss_minutes:.0f}, ложные {v.false_alarm_minutes:.0f}, "
                f"заблаговременность {_minutes(v.lead_time_minutes)} -> {path}"
            )
    versions = {run.result.algorithm_version for run in runs}
    (out_dir / "README.md").write_text(readme(runs, periods, control, versions), encoding="utf-8")
    print(f"сводка: {out_dir / 'README.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
