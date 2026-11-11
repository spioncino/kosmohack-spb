// Текущий отчёт CelesTrak SOCRATES по МКС (GET /conjunctions/latest): состояние карточки, порядок строк и подписи.
// Уровни строк приходят из API (D53) и здесь не пересчитываются.
import type { ConjunctionLevel, ConjunctionRow, ConjunctionsLatest, Mode } from '../api/client';
import { ago, ms, parseUtc } from './format';

// missing: отчёта нет; empty: отчёт есть, но в нём только со-орбитальные объекты или ничего; list: есть сближения.
export type ReportState = 'missing' | 'empty' | 'list';

export function reportState(report: ConjunctionsLatest): ReportState {
  if (!report.available) return 'missing';
  return report.conjunctions.length ? 'list' : 'empty';
}

// Строки по времени наибольшего сближения, от ближайшего; исходный массив не меняется.
export function sortByTca(rows: readonly ConjunctionRow[]): ConjunctionRow[] {
  return [...rows].sort((a, b) => ms(a.tca) - ms(b.tca));
}

// MAXPROB в экспоненциальной записи с четырьмя значащими цифрами, как в отчёте SOCRATES: «3.045e-05».
export function fmtMaxprob(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  return value.toExponential(3).replace(/e([+-])(\d)$/, 'e$10$2');
}

const MONTHS = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];

function pad(value: number): string {
  return String(value).padStart(2, '0');
}

// «18 сен 22:16» по UTC; год добавляется, если он не совпадает с годом now.
export function fmtDay(value: string, now?: number): string {
  const date = parseUtc(value);
  if (Number.isNaN(date.getTime())) return '—';
  const year = now !== undefined && new Date(now).getUTCFullYear() !== date.getUTCFullYear() ? ` ${date.getUTCFullYear()}` : '';
  return `${date.getUTCDate()} ${MONTHS[date.getUTCMonth()]}${year} ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}`;
}

export const NO_REPORT_TEXT = 'отчёта SOCRATES нет';
export const NO_REPORT_NOTE = 'Отсутствие отчёта не означает отсутствие сближений.';

// «отчёт на 18 сен 22:16 UTC, 15 ч назад»; давность считается от now, чтобы подпись не застывала на открытой странице.
export function reportTimeText(report: ConjunctionsLatest, now: number): string {
  if (!report.available) return NO_REPORT_TEXT;
  if (!report.data_as_of) return 'время отчёта неизвестно';
  return `отчёт на ${fmtDay(report.data_as_of, now)} UTC, ${ago(report.data_as_of, now)}`;
}

// Интервал расчёта отчёта: «интервал расчёта 17 сен 22:00 – 24 сен 22:00 UTC (7 сут)».
export function intervalText(report: ConjunctionsLatest, now?: number): string | null {
  if (!report.available || !report.interval_start || !report.interval_stop) return null;
  const days = Math.round((ms(report.interval_stop) - ms(report.interval_start)) / 86400000);
  return `интервал расчёта ${fmtDay(report.interval_start, now)} – ${fmtDay(report.interval_stop, now)} UTC (${days} сут)`;
}

// Давность в часах на момент now; без времени отчёта null.
export function ageHours(report: ConjunctionsLatest, now: number): number | null {
  if (!report.data_as_of) return report.age_hours;
  return (now - ms(report.data_as_of)) / 3600000;
}

// Отчёт устарел по признаку API или потому, что за время открытой страницы стал старше предела.
export function isStale(report: ConjunctionsLatest, now: number): boolean {
  if (!report.available) return false;
  if (report.stale) return true;
  const age = ageHours(report, now);
  return age !== null && age > report.max_report_age_hours;
}

export function staleHint(report: ConjunctionsLatest): string {
  return `отчёт старше ${report.max_report_age_hours} ч: сближения из него текущими не считаются`;
}

// Пустой список при наличии отчёта: сближений в нём нет, но это не «сближений нет вообще» без отчёта.
export function emptyText(report: ConjunctionsLatest): string {
  return report.coorbiting_rows > 0 ? 'в отчёте нет сближений, кроме со-орбитальных объектов' : 'в отчёте нет сближений';
}

// Пороги уровня по MAXPROB как верхней оценке Pc (D53).
export const LEVEL_HINT: Record<ConjunctionLevel, string> = {
  high: 'MAXPROB не ниже 1e-4: красный порог правил полёта МКС не исключён (D53)',
  elevated: 'MAXPROB не ниже 1e-5: жёлтый порог правил полёта МКС не исключён (D53)',
  none: 'MAXPROB ниже 1e-5: Pc тем более ниже жёлтого порога правил полёта МКС (D53)',
};

export function isPast(tca: string, now: number): boolean {
  return ms(tca) < now;
}

// Разбор и прогноз из прошлого: архива SOCRATES нет, линия сближений в них не считается (D29, D30).
export function isHistorical(mode: Mode): boolean {
  return mode === 'review' || mode === 'replay';
}

export const HISTORICAL_TEXT =
  'Для исторических дат сближений нет ни у кого: CelesTrak SOCRATES хранит только текущий отчёт, архива нет (D29). ' +
  'Текущие сближения на ближайшие 7 суток показаны выше.';
