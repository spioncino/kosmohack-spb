import type { AssessmentRequest, AssessmentResult, Constraints, Window } from '../api/client';
import { fmtWindow, ms, num } from './format';
import { searchPeriodFrom, startPeriodError, startPeriodOf } from './period';
import { maxHours } from './score';

// Та же постановка с другой длительностью ВКД. Период допустимого начала не меняется, конец периода поиска
// пересчитывается: search_end = конец периода допустимого начала + новая длительность. null, если запрос не
// проходит проверку API (отрезок начал от 15 минут до 24 часов, начало ВКД внутри него).
export function withDuration(request: AssessmentRequest, hours: number): AssessmentRequest | null {
  const period = startPeriodOf(request);
  const next: AssessmentRequest = { ...request, duration_hours: hours, ...searchPeriodFrom(period.start, period.hours, hours) };
  return startPeriodError(next) === null ? next : null;
}

function sameInstant(a: string | null | undefined, b: string | null | undefined): boolean {
  if (!a || !b) return !a && !b;
  return ms(a) === ms(b);
}

// Одинаковый период допустимого начала: те же search_start и search_end − длительность.
function sameStartPeriod(a: AssessmentRequest, b: AssessmentRequest): boolean {
  const pa = startPeriodOf(a);
  const pb = startPeriodOf(b);
  return ms(pa.start) === ms(pb.start) && ms(pa.end) === ms(pb.end);
}

// Отсутствующие ограничения и ограничения со значениями по умолчанию равны.
function sameConstraints(a: Constraints | null | undefined, b: Constraints | null | undefined): boolean {
  return (a?.min_sunlit_fraction ?? null) === (b?.min_sunlit_fraction ?? null) && !!a?.start_in_daylight === !!b?.start_in_daylight;
}

// Новый запрос отличается от прежнего только длительностью: тот же режим, отсечение, старт ВКД, ограничения
// и период допустимого начала (конец периода поиска при этом сдвигается на разницу длительностей, как в
// withDuration). По постановке задачи это изменение плана работ.
export function isDurationChange(previous: AssessmentRequest, next: AssessmentRequest): boolean {
  if (previous.duration_hours === next.duration_hours) return false;
  if (previous.mode !== next.mode || !sameInstant(previous.cutoff, next.cutoff) || !sameInstant(previous.eva_start, next.eva_start)) return false;
  if (!sameConstraints(previous.constraints, next.constraints)) return false;
  return sameStartPeriod(previous, next);
}

export interface PlanSummary {
  id: string;
  hours: number;
  // Предпочтительное окно; null, когда оснований для выбора недостаточно.
  preferred: Window | null;
  // Минуты high + critical в предпочтительном окне, максимум по линиям; null без окна или без линий в сравнении.
  highMinutes: number | null;
}

export interface PlanChange {
  from: PlanSummary;
  to: PlanSummary;
}

export function planSummary(result: AssessmentResult): PlanSummary {
  const rec = result.recommendation;
  const preferred = rec.insufficient ? null : rec.preferred;
  const score = preferred
    ? [...result.windows, result.requested_window].find((s) => ms(s.window.start) === ms(preferred.start) && ms(s.window.end) === ms(preferred.end))
    : undefined;
  const highMinutes = score && Object.keys(score.per_line).length ? Math.round(maxHours(score.per_line, ['high', 'critical']) * 60) : null;
  return { id: result.id, hours: result.request.duration_hours, preferred, highMinutes };
}

// Изменение плана между прежним открытым расчётом и новым; null, если запросы отличаются не только длительностью.
export function detectPlanChange(previous: AssessmentResult | null, next: AssessmentResult): PlanChange | null {
  if (!previous || previous.id === next.id || !isDurationChange(previous.request, next.request)) return null;
  return { from: planSummary(previous), to: planSummary(next) };
}

// Изменение плана сохраняется, пока открыт один из двух его расчётов; открытие другого расчёта его снимает.
export function keepPlanChange(change: PlanChange | null, openedId: string): PlanChange | null {
  return change && (openedId === change.from.id || openedId === change.to.id) ? change : null;
}

export const hoursText = (hours: number): string => `${num(hours, Number.isInteger(hours) ? 0 : 1)} ч`;

export interface PlanBanner {
  title: string;
  rows: { label: string; text: string }[];
  note: string;
}

function summaryText(plan: PlanSummary): string {
  if (!plan.preferred) return 'оснований для выбора окна недостаточно';
  const minutes = plan.highMinutes === null ? 'нет данных' : `${plan.highMinutes} мин (максимум по линиям)`;
  return `предпочтительное окно ${fmtWindow(plan.preferred)}, high + critical в нём ${minutes}`;
}

// Текст баннера «изменение плана»: длительность до и после, окно и минуты high + critical каждого плана и правило,
// по которому окна разной длительности не сравниваются.
export function planChangeBanner(change: PlanChange): PlanBanner {
  const shorter = change.to.hours < change.from.hours;
  return {
    title: `Изменение плана: длительность ${hoursText(change.from.hours)} → ${hoursText(change.to.hours)}`,
    rows: [
      { label: `Прежний план, ${hoursText(change.from.hours)}`, text: summaryText(change.from) },
      { label: `Новый план, ${hoursText(change.to.hours)}`, text: summaryText(change.to) },
    ],
    note: shorter
      ? 'Окна сравниваются только внутри одной длительности: меньше минут после сокращения даёт более короткая работа, а не лучшие условия.'
      : 'Окна сравниваются только внутри одной длительности: больше минут после увеличения даёт более длинная работа, а не худшие условия.',
  };
}
