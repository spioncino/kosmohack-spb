import type { AssessmentResult, BaselineCheck, Interval, Level, Provenance, Verification, Window } from '../api/client';
import { fmtUtc, fmtWindow, minutesLabel } from './format';
import { KIND_LABEL, lineLabel } from './labels';

// Подпись над таблицей сравнения: условия одинаковы для всех подходов, простой подход не различает окна суток.
export const COMPARISON_CAPTION = 'Все подходы видят только данные, опубликованные до отсечения, и сверяются с одним фактом одной функцией. '
  + 'Простой подход предсказывает один уровень на весь период и не знает орбиты, поэтому окна внутри суток различить не может.';

// Правило нашего расчёта по линии протонов (src/evarisk/risk/README.md): уровень в точке трассы с доступом частиц.
export const OUR_RULE = 'уровень в каждой точке трассы станции с учётом геомагнитного доступа';

const NO_DATA = 'нет данных';
const NO_WINDOW = 'оснований для выбора нет';

export interface ComparisonRow {
  key: string;
  title: string;
  ours: boolean;
  rule: string;
  note: string;
  // Наибольший уровень, который подход предсказал на период поиска; null без интервалов прогноза.
  level: Level | null;
  basis: string;
  provenance: Provenance[];
  window: string;
  // Минуты экспозиции по факту в выбранном окне; null без данных или без окна.
  exposureMinutes: number | null;
  exposure: string;
  counts: string;
  lead: string;
  stable: boolean;
}

const LEVEL_RANK: Record<Level, number> = { unknown: -1, none: 0, elevated: 1, high: 2, critical: 3 };

// Наибольший уровень интервалов; unknown, только когда известного уровня нет ни в одном интервале.
export function highestLevel(intervals: Interval[]): Level | null {
  if (!intervals.length) return null;
  return intervals.reduce<Level>((best, iv) => (LEVEL_RANK[iv.level] > LEVEL_RANK[best] ? iv.level : best), 'unknown');
}

const minutesValue = (value: number | null | undefined): string => (value === null || value === undefined ? NO_DATA : String(Math.round(value)));

// Попадания, пропуски и ложные тревоги по минутам экспозиции через косую черту; без всех трёх чисел «нет данных».
export function exposureCounts(hit: number | null | undefined, miss: number | null | undefined, falseAlarm: number | null | undefined): string {
  const values = [hit, miss, falseAlarm];
  if (values.every((value) => value === null || value === undefined)) return NO_DATA;
  return values.map(minutesValue).join(' / ');
}

function chosen(window: Window | null, insufficient: boolean): Window | null {
  return insufficient ? null : window;
}

function windowText(window: Window | null, insufficient: boolean): string {
  if (window) return fmtWindow(window);
  return insufficient ? NO_WINDOW : NO_DATA;
}

// Экспозиция в выбранном окне: без окна «окна нет», без числа «нет данных».
function exposureText(window: Window | null, minutes: number | null | undefined): string {
  if (!window) return 'окна нет';
  return minutesValue(minutes);
}

function uniqueProvenance(intervals: Interval[]): Provenance[] {
  const seen = new Map<string, Provenance>();
  for (const iv of intervals) for (const p of iv.provenance) if (!seen.has(p.raw_id)) seen.set(p.raw_id, p);
  return [...seen.values()];
}

// Основание нашего прогноза берётся из интервалов сверки: число интервалов, их kind и момент отсечения.
function ourBasis(v: Verification, cutoff: string | null): string {
  const line = lineLabel(v.line);
  if (!v.predicted.length) return `интервалов прогноза по линии «${line}» нет`;
  const kinds = [...new Set(v.predicted.map((iv) => KIND_LABEL[iv.kind]))].join(', ');
  return `интервалы линии «${line}»: ${v.predicted.length} (${kinds})${cutoff ? `, данные до отсечения ${fmtUtc(cutoff)}` : ''}`;
}

function ourRow(result: AssessmentResult, v: Verification): ComparisonRow {
  const rec = result.recommendation;
  const insufficient = rec.insufficient || !rec.preferred;
  const window = chosen(rec.preferred, insufficient);
  const exposureMinutes = window ? v.preferred_exposure_minutes ?? null : null;
  return {
    key: 'ours',
    title: 'наш расчёт',
    ours: true,
    rule: OUR_RULE,
    note: '',
    level: highestLevel(v.predicted),
    basis: ourBasis(v, result.request.cutoff),
    provenance: uniqueProvenance(v.predicted),
    window: windowText(window, insufficient),
    exposureMinutes,
    exposure: exposureText(window, v.preferred_exposure_minutes),
    counts: exposureCounts(v.exposure_hit_minutes, v.exposure_miss_minutes, v.exposure_false_alarm_minutes),
    lead: minutesLabel(v.lead_time_minutes),
    stable: v.window_choice_stable,
  };
}

function baselineRow(b: BaselineCheck): ComparisonRow {
  const insufficient = b.insufficient || !b.preferred;
  const window = chosen(b.preferred, insufficient);
  return {
    key: b.name,
    title: b.title,
    ours: false,
    rule: b.rule,
    note: b.note,
    level: b.level,
    basis: b.basis,
    provenance: b.provenance,
    window: windowText(window, insufficient),
    exposureMinutes: window ? b.preferred_exposure_minutes : null,
    exposure: exposureText(window, b.preferred_exposure_minutes),
    counts: exposureCounts(b.exposure_hit_minutes, b.exposure_miss_minutes, b.exposure_false_alarm_minutes),
    lead: minutesLabel(b.lead_time_minutes),
    stable: b.window_choice_stable,
  };
}

// Строки таблицы «Сравнение с простыми подходами»: наш расчёт и по строке на простой подход.
// Без сверки или без простых подходов (у ранних расчётов поля нет) таблицы нет.
export function comparisonRows(result: AssessmentResult): ComparisonRow[] {
  const v = result.verification;
  if (!v || !v.baselines?.length) return [];
  return [ourRow(result, v), ...v.baselines.map(baselineRow)];
}

// Строка «в запрошенном окне по факту»; у расчётов без поля строки нет, null в поле это «нет данных».
export function requestedExposureText(result: AssessmentResult): string | null {
  const v = result.verification;
  if (!v || v.requested_exposure_minutes === undefined) return null;
  const minutes = v.requested_exposure_minutes;
  const value = minutes === null ? NO_DATA : `${Math.round(minutes)} мин экспозиции`;
  return `В запрошенном окне ${fmtWindow(result.requested_window.window)} по факту: ${value}`;
}
