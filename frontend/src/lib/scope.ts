import type { AssessmentResult, Level, TrajectorySample } from '../api/client';
import { ms } from './format';
import { startPeriodOf } from './period';

// Период карты и его разметка: чистые функции для сцены, шкалы периода и списка проходов.

export interface Span {
  start: number;
  end: number;
}

export interface LevelRun extends Span {
  level: Level;
}

export interface DangerPass extends Span {
  level: Level;
  lat: number;
  lon: number;
  maxAbsMlat: number | null;
}

export type FocusKind = 'requested' | 'preferred' | 'period';

const DANGER: Level[] = ['high', 'critical'];
const RANK: Record<Level, number> = { unknown: 0, none: 1, elevated: 2, high: 3, critical: 4 };

export const isDanger = (level: Level): boolean => DANGER.includes(level);

export function spanOf(window: { start: string; end: string }): Span {
  return { start: ms(window.start), end: ms(window.end) };
}

export const inSpan = (t: number, span: Span): boolean => t >= span.start && t <= span.end;

export function periodOf(a: AssessmentResult): Span {
  return { start: ms(a.request.search_start), end: ms(a.request.search_end) };
}

// Период допустимого начала расчёта: от search_start до search_end − длительность (lib/period.ts).
export function startsOf(a: AssessmentResult): Span {
  return spanOf(startPeriodOf(a.request));
}

// Запрошенное и рекомендованное окна расчёта; moved истинно, когда рекомендовано другое окно.
export function windowsOf(a: AssessmentResult): { requested: Span; preferred: Span | null; moved: boolean } {
  const requested = spanOf(a.requested_window.window);
  const p = a.recommendation.insufficient ? null : a.recommendation.preferred;
  const preferred = p ? spanOf(p) : null;
  return { requested, preferred, moved: !!preferred && (preferred.start !== requested.start || preferred.end !== requested.end) };
}

// Серии подряд идущих сэмплов одного уровня; серия кончается там, где начинается следующая.
export function levelRuns(samples: TrajectorySample[]): LevelRun[] {
  const runs: LevelRun[] = [];
  for (const s of samples) {
    const t = ms(s.t);
    const last = runs[runs.length - 1];
    if (last && last.level === s.level) last.end = t;
    else {
      if (last) last.end = t;
      runs.push({ start: t, end: t, level: s.level });
    }
  }
  return runs;
}

// Проходы уровня high и critical: соседние опасные серии сливаются в один проход с наибольшим уровнем,
// положение берётся у сэмпла с наибольшей магнитной широтой по модулю, иначе у среднего.
export function dangerPasses(samples: TrajectorySample[]): DangerPass[] {
  const passes: DangerPass[] = [];
  let run: TrajectorySample[] = [];
  const flush = (endT: number) => {
    if (!run.length) return;
    const known = run.filter((s) => s.mlat_deg !== null);
    const peak = known.length ? known.reduce((a, b) => (Math.abs(b.mlat_deg!) > Math.abs(a.mlat_deg!) ? b : a)) : run[Math.floor(run.length / 2)];
    const level = run.reduce<Level>((a, s) => (RANK[s.level] > RANK[a] ? s.level : a), 'high');
    passes.push({ start: ms(run[0].t), end: endT, level, lat: peak.lat_deg, lon: peak.lon_deg, maxAbsMlat: known.length ? Math.abs(peak.mlat_deg!) : null });
    run = [];
  };
  for (const s of samples) {
    if (isDanger(s.level)) run.push(s);
    else flush(ms(s.t));
  }
  if (run.length) flush(ms(run[run.length - 1].t));
  return passes;
}

export function samplesIn(samples: TrajectorySample[], span: Span): TrajectorySample[] {
  return samples.filter((s) => inSpan(ms(s.t), span));
}

// Минуты уровней high и critical внутри окна по сериям уровней.
export function dangerMinutes(runs: LevelRun[], span: Span): number {
  let total = 0;
  for (const r of runs) {
    if (!isDanger(r.level)) continue;
    total += Math.max(0, Math.min(r.end, span.end) - Math.max(r.start, span.start));
  }
  return Math.round(total / 60000);
}

// Доля положения момента внутри периода, от 0 до 1, с обрезкой по краям.
export function fraction(t: number, span: Span): number {
  if (span.end <= span.start) return 0;
  return Math.min(1, Math.max(0, (t - span.start) / (span.end - span.start)));
}

// Часовые отметки шкалы: шаг 1 ч для периода до 8 ч, 3 ч до 36 ч, иначе 6 ч; отметки на целых часах UTC.
export function hourTicks(span: Span): number[] {
  const hours = (span.end - span.start) / 3600000;
  const step = (hours <= 8 ? 1 : hours <= 36 ? 3 : 6) * 3600000;
  const ticks: number[] = [];
  for (let t = Math.ceil(span.start / step) * step; t <= span.end; t += step) ticks.push(t);
  return ticks;
}
