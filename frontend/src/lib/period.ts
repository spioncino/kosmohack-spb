import type { AssessmentRequest } from '../api/client';
import { fmtWindow, isoZ, ms } from './format';

// Период поиска задаёт допустимые моменты начала ВКД (глоссарий постановки): окно может начаться от search_start
// до search_end − длительность. Форма расчёта спрашивает именно этот период, API получает search_start и search_end.

const MINUTE_MS = 60000;
const HOUR_MS = 3600000;
// Шаг сетки окон 15 мин (config/windows.toml): отрезок начал не короче шага даёт не меньше двух окон.
export const MIN_START_MINUTES = 15;
export const MAX_START_HOURS = 24;

type PeriodFields = Pick<AssessmentRequest, 'search_start' | 'search_end' | 'duration_hours'>;

export interface StartPeriod {
  start: string;
  end: string;
  hours: number;
}

// Период допустимого начала запроса: от search_start до search_end − duration_hours.
export function startPeriodOf(request: PeriodFields): StartPeriod {
  const start = ms(request.search_start);
  const end = ms(request.search_end) - request.duration_hours * HOUR_MS;
  return { start: isoZ(start), end: isoZ(end), hours: (end - start) / HOUR_MS };
}

// Период поиска для API из периода допустимого начала: search_end = начало + длина периода + длительность ВКД.
// Сумма часов переводится в целые минуты, чтобы дробные часы не давали секунд в запросе.
export function searchPeriodFrom(startIso: string, startHours: number, durationHours: number): Pick<AssessmentRequest, 'search_start' | 'search_end'> {
  const start = ms(startIso);
  return { search_start: isoZ(start), search_end: isoZ(start + Math.round((startHours + durationHours) * 60) * MINUTE_MS) };
}

export const START_SPAN_ERROR =
  'Период допустимого начала должен занимать от 15 минут до 24 часов: постановка требует не менее двух окон и период поиска до 24 часов';
export const START_OUTSIDE_ERROR = 'Начало ВКД должно лежать в периоде допустимого начала';

// Та же проверка, что в API (AssessmentRequest в src/evarisk/api/schemas.py): отрезок начал от 15 минут до 24 часов
// включительно и начало ВКД внутри него, границы включены. null, если запрос проходит.
export function startPeriodError(request: PeriodFields & Pick<AssessmentRequest, 'eva_start'>): string | null {
  const start = ms(request.search_start);
  const end = ms(request.search_end) - request.duration_hours * HOUR_MS;
  const eva = ms(request.eva_start);
  if ([start, end, eva].some((value) => Number.isNaN(value))) return 'Заполните дату и время начала ВКД и периода допустимого начала';
  const span = end - start;
  if (!(span >= MIN_START_MINUTES * MINUTE_MS && span <= MAX_START_HOURS * HOUR_MS)) return START_SPAN_ERROR;
  if (!(eva >= start && eva <= end)) return `${START_OUTSIDE_ERROR}: ${fmtWindow({ start: isoZ(start), end: isoZ(end) })}`;
  return null;
}

// «допустимое начало 2024-05-11 00:00–18:00 UTC»
export function startPeriodText(request: PeriodFields): string {
  return `допустимое начало ${fmtWindow(startPeriodOf(request))}`;
}

// «окна 2024-05-11 00:00 – 2024-05-12 00:00 UTC»: от первого начала до последнего конца окна.
export function windowsSpanText(request: PeriodFields): string {
  return `окна ${fmtWindow({ start: request.search_start, end: request.search_end })}`;
}

// Две подписи вместе: где могут начинаться окна и что они покрывают.
export function periodCaption(request: PeriodFields): string {
  return `${startPeriodText(request)} · ${windowsSpanText(request)}`;
}
