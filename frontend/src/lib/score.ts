import type { AssessmentResult, Interval, Level, LineAssessment } from '../api/client';
import { isoZ, ms } from './format';
import { LINE_ORDER, lineLabel } from './labels';

export type BadgeColor = 'green' | 'yellow' | 'red' | 'gray';

export type LevelMinutes = Record<Level, number>;

// Запрошенное окно ВКД для одной линии: границы и, если есть, часы по уровням из requested_window.per_line.
export interface RequestedSpan {
  start: string;
  end: string;
  hours?: Partial<Record<Level, number>> | null;
}

export interface LineScore {
  line: string;
  label: string;
  availability: LineAssessment['availability'];
  // Доля минут периода поиска без уровней high и critical, %; null при отсутствии данных. На цвет не влияет.
  percent: number | null;
  // Доля минут периода поиска, покрытых интервалами с известным уровнем.
  completeness: number;
  badMinutes: number;
  totalMinutes: number;
  // Запрошенное окно; null, если его нельзя определить по расчёту.
  window: { start: string; end: string } | null;
  // Минуты по уровням в запрошенном окне: из requested_window.per_line или из интервалов линии.
  windowMinutes: LevelMinutes | null;
  windowSource: 'per_line' | 'intervals' | null;
  // Минуты high + critical в запрошенном окне; null без окна или без данных по линии.
  windowDangerMinutes: number | null;
  // Минуты запрошенного окна, не покрытые ни одним интервалом.
  windowUncoveredMinutes: number | null;
  // Минуты периода поиска без измеренного потока (metrics.unmeasured_minutes, D64): уровень на них взят из внешнего
  // прогноза и сообщений о событиях. null, если у линии такой метрики нет.
  unmeasuredMinutes: number | null;
  // Минуты запрошенного окна в интервалах kind external_forecast у линии с метрикой unmeasured_minutes; иначе null.
  windowForecastMinutes: number | null;
  color: BadgeColor;
  // Причина цвета плашки одной фразой.
  reason: string;
}

const LEVELS: Level[] = ['none', 'elevated', 'high', 'critical', 'unknown'];
// Меньше этого числа минут это погрешность сложения дробных часов, а не уровень в окне.
const MINUTE_EPS = 1e-3;
// Полнота периода поиска, начиная с которой данные считаются полными.
const FULL_COMPLETENESS = 0.999;

// Правило цвета для подсказок интерфейса; то же правило описано в README («Правила интерфейса»).
export const BADGE_RULE =
  'Цвет плашки по запрошенному окну ВКД: красная, если в окне есть high или critical; жёлтая, если в окне есть elevated или unknown, ' +
  'часть окна не покрыта интервалами, период поиска покрыт данными не полностью, поток протонов не измерен хотя бы часть периода ' +
  '(уровень там взят из внешнего прогноза) или окно не определено; зелёная, только если в окне только none при полных измеренных ' +
  'данных; серая «нет данных», если линия недоступна. Процент это доля минут всего периода поиска без high и critical, на цвет он не влияет.';

function overlapMinutes(interval: Interval, start: number, end: number): number {
  const a = Math.max(ms(interval.start), start);
  const b = Math.min(ms(interval.end), end);
  return b > a ? (b - a) / 60000 : 0;
}

function emptyMinutes(): LevelMinutes {
  return { none: 0, elevated: 0, high: 0, critical: 0, unknown: 0 };
}

// Минуты по уровням в окне: часы per_line, если они есть, иначе пересечение интервалов линии с окном.
export function windowLevelMinutes(line: LineAssessment, span: RequestedSpan): { minutes: LevelMinutes; source: 'per_line' | 'intervals' } {
  const minutes = emptyMinutes();
  if (span.hours) {
    for (const level of LEVELS) minutes[level] = (span.hours[level] || 0) * 60;
    return { minutes, source: 'per_line' };
  }
  const start = ms(span.start);
  const end = ms(span.end);
  for (const interval of line.intervals) minutes[interval.level] += overlapMinutes(interval, start, end);
  return { minutes, source: 'intervals' };
}

// Красная при high или critical в окне; жёлтая при elevated, unknown, пробеле в окне, неполных данных периода
// (включая неизмеренный поток) или неизвестном окне; зелёная только при одном none в окне и полных данных; серая без
// данных по линии.
export function badgeColor(percent: number | null, windowMinutes: LevelMinutes | null, complete: boolean): BadgeColor {
  if (percent === null) return 'gray';
  if (windowMinutes && (windowMinutes.critical > MINUTE_EPS || windowMinutes.high > MINUTE_EPS)) return 'red';
  if (!windowMinutes || !complete || windowMinutes.elevated > MINUTE_EPS || windowMinutes.unknown > MINUTE_EPS) return 'yellow';
  return 'green';
}

// Минуты для подписи: дробная ненулевая доля минуты показывается как «<1», а не как 0.
export function fmtMinutes(minutes: number): string {
  if (minutes <= MINUTE_EPS) return '0';
  return minutes < 0.5 ? '<1' : String(Math.round(minutes));
}

// Поток линии не измерен хотя бы на часть периода поиска: уровень там взят из внешнего прогноза (D64).
export function hasUnmeasured(score: Pick<LineScore, 'unmeasuredMinutes'> | null | undefined): boolean {
  return !!score && (score.unmeasuredMinutes ?? 0) > MINUTE_EPS;
}

// «поток не измерен на 720 из 720 мин периода поиска, уровень взят из внешнего прогноза (в запрошенном окне 360 из
// 360 мин), а не из измерений»; null, если поток измерен весь период или метрики нет.
export function unmeasuredText(score: Pick<LineScore, 'unmeasuredMinutes' | 'totalMinutes' | 'windowForecastMinutes' | 'window'> | null | undefined): string | null {
  if (!score || !hasUnmeasured(score)) return null;
  const period = `поток не измерен на ${fmtMinutes(score.unmeasuredMinutes!)} из ${fmtMinutes(score.totalMinutes)} мин периода поиска`;
  const forecast = score.windowForecastMinutes ?? 0;
  if (score.window && forecast > MINUTE_EPS) {
    const length = (ms(score.window.end) - ms(score.window.start)) / 60000;
    return `${period}, уровень взят из внешнего прогноза (в запрошенном окне ${fmtMinutes(forecast)} из ${fmtMinutes(length)} мин), а не из измерений`;
  }
  return `${period}, там уровень взят из внешнего прогноза, а не из измерений`;
}

// Почему у линии нет оценки: нет архива источника на эти даты это не поломка и не пропуск данных.
function grayReason(line: string, availability: LineAssessment['availability']): string {
  if (availability === 'unusable_for_replay') {
    const why =
      line === 'conjunctions'
        ? '; CelesTrak SOCRATES публикует только текущий отчёт на 7 суток вперёд, сближения считаются в режиме «Сейчас»'
        : '';
    return `у источника нет архива на эти даты, в сравнении окон линия не участвует${why}`;
  }
  if (availability === 'stale') return 'данные источника устарели, уровень не выставлен';
  return 'данных по линии нет';
}

// Подпись плашки линии без оценки: «нет архива» отличается от «нет данных».
function noScoreLabel(availability: LineAssessment['availability']): string {
  if (availability === 'unusable_for_replay') return 'нет архива за эти даты';
  if (availability === 'stale') return 'данные устарели';
  return 'нет данных';
}

function colorReason(score: Omit<LineScore, 'color' | 'reason'>, color: BadgeColor): string {
  if (color === 'gray') return grayReason(score.line, score.availability);
  const w = score.windowMinutes;
  if (!w) return 'запрошенное окно не определено';
  const parts = (levels: Level[]) => levels.filter((level) => w[level] > MINUTE_EPS).map((level) => `${level} ${fmtMinutes(w[level])} мин`);
  if (color === 'red') return `в запрошенном окне ${parts(['critical', 'high']).join(', ')}`;
  if (color === 'green') return 'в запрошенном окне только none, данные полные';
  const reasons = parts(['elevated', 'unknown']).map((part) => `в запрошенном окне ${part}`);
  if ((score.windowUncoveredMinutes || 0) > MINUTE_EPS) reasons.push(`${fmtMinutes(score.windowUncoveredMinutes!)} мин окна без интервалов`);
  if (score.completeness < FULL_COMPLETENESS) reasons.push(`полнота периода поиска ${Math.floor(score.completeness * 100)} %`);
  const unmeasured = unmeasuredText(score);
  if (unmeasured) reasons.push(reasons.length ? unmeasured : `в запрошенном окне только none, но ${unmeasured}`);
  return reasons.join('; ');
}

export function lineScore(line: LineAssessment, searchStart: string, searchEnd: string, span: RequestedSpan | null = null): LineScore {
  const start = ms(searchStart);
  const end = ms(searchEnd);
  const totalMinutes = Math.max(0, (end - start) / 60000);
  let badMinutes = 0;
  let knownMinutes = 0;
  for (const interval of line.intervals) {
    const minutes = overlapMinutes(interval, start, end);
    if (interval.level === 'high' || interval.level === 'critical') badMinutes += minutes;
    if (interval.level !== 'unknown') knownMinutes += minutes;
  }
  const completeness = totalMinutes > 0 ? Math.min(1, knownMinutes / totalMinutes) : 0;
  const available = line.availability === 'available' && totalMinutes > 0;
  const percent = available ? Math.max(0, Math.min(100, (1 - badMinutes / totalMinutes) * 100)) : null;

  const spanValid = span !== null && ms(span.end) > ms(span.start);
  const inWindow = spanValid ? windowLevelMinutes(line, span!) : null;
  const windowMinutes = inWindow ? inWindow.minutes : null;
  const windowLength = spanValid ? (ms(span!.end) - ms(span!.start)) / 60000 : 0;
  const windowUncoveredMinutes = windowMinutes ? Math.max(0, windowLength - LEVELS.reduce((acc, level) => acc + windowMinutes[level], 0)) : null;
  const windowDangerMinutes = windowMinutes && percent !== null ? windowMinutes.high + windowMinutes.critical : null;
  const unmeasured = line.metrics?.unmeasured_minutes;
  const unmeasuredMinutes = typeof unmeasured === 'number' && Number.isFinite(unmeasured) ? unmeasured : null;
  const windowForecastMinutes =
    unmeasuredMinutes !== null && spanValid
      ? line.intervals.filter((interval) => interval.kind === 'external_forecast').reduce((acc, interval) => acc + overlapMinutes(interval, ms(span!.start), ms(span!.end)), 0)
      : null;
  // Уровень по внешнему прогнозу вместо измеренного потока это не полные данные: зелёной плашки нет (D64).
  const complete = completeness >= FULL_COMPLETENESS && (windowUncoveredMinutes ?? 0) <= MINUTE_EPS && (unmeasuredMinutes ?? 0) <= MINUTE_EPS;

  const base = {
    line: line.line,
    label: lineLabel(line.line),
    availability: line.availability,
    percent,
    completeness,
    badMinutes,
    totalMinutes,
    window: spanValid ? { start: span!.start, end: span!.end } : null,
    windowMinutes,
    windowSource: inWindow ? inWindow.source : null,
    windowDangerMinutes,
    windowUncoveredMinutes,
    unmeasuredMinutes,
    windowForecastMinutes,
  };
  const color = badgeColor(percent, windowMinutes, complete);
  return { ...base, color, reason: colorReason(base, color) };
}

// Запрошенное окно расчёта для линии: границы requested_window.window, иначе eva_start + duration_hours;
// часы по уровням берутся из requested_window.per_line, если линия там есть.
export function requestedSpan(result: AssessmentResult, line: string): RequestedSpan | null {
  const requested = result.requested_window;
  let start = requested?.window?.start || null;
  let end = requested?.window?.end || null;
  if (!start || !end) {
    const evaStart = result.request?.eva_start;
    const hours = result.request?.duration_hours;
    if (!evaStart || !(hours > 0)) return null;
    start = evaStart;
    end = isoZ(ms(evaStart) + hours * 3600000);
  }
  return { start, end, hours: requested?.per_line?.[line] ?? null };
}

export function lineScores(result: AssessmentResult): LineScore[] {
  const byName = new Map(result.lines.map((line) => [line.line, line]));
  const ordered = [...LINE_ORDER.filter((name) => byName.has(name)), ...result.lines.map((l) => l.line).filter((n) => !LINE_ORDER.includes(n as never))];
  return ordered
    .filter((name) => name !== GEOMETRY_LINE)
    .map((name) => lineScore(byName.get(name)!, result.request.search_start, result.request.search_end, requestedSpan(result, name)));
}

// Оценки по имени линии: вкладки и списки ищут оценку по линии, а не по позиции (у геометрии доступа оценки нет).
export function scoresByLine(scores: LineScore[]): Map<string, LineScore> {
  return new Map(scores.map((score) => [score.line, score]));
}

// Процент для показа округляется вниз: 99,7 % не превращается в 100 % при минутах high или critical в периоде.
export function periodPercent(score: LineScore | null | undefined): number | null {
  return score && score.percent !== null ? Math.floor(score.percent) : null;
}

// Короткая подпись под процентом: «поток не измерен: 720 из 720 мин периода, уровень по прогнозу»; null без неизмеренных минут.
export function unmeasuredLabel(score: LineScore | null | undefined): string | null {
  if (!score || !hasUnmeasured(score)) return null;
  return `поток не измерен: ${fmtMinutes(score.unmeasuredMinutes!)} из ${fmtMinutes(score.totalMinutes)} мин периода, уровень по прогнозу`;
}

// Фраза для карточек расчёта (полнота на /eva и /compare): полнота считает минуты с уровнем, а уровень по неизмеренному
// потоку взят из прогноза. null, если поток измерен весь период.
export function unmeasuredNote(scores: LineScore[]): string | null {
  const score = scores.find((item) => item.percent !== null && hasUnmeasured(item));
  if (!score) return null;
  return `${score.label}: ${unmeasuredText(score)}. Полнота считает минуты с уровнем, измерен он или взят из прогноза.`;
}

// Подпись второго числа плашки: минуты high + critical в запрошенном окне.
export function windowLabel(score: LineScore | null | undefined): string {
  if (!score) return 'без оценки';
  if (score.percent === null) return noScoreLabel(score.availability);
  if (score.windowDangerMinutes === null) return 'запрошенное окно не определено';
  return `в запрошенном окне: ${fmtMinutes(score.windowDangerMinutes)} мин high+critical`;
}

export interface EventItem {
  id: string;
  line: string;
  category: string;
  level: Level;
  kind: Interval['kind'];
  start: string;
  end: string;
  rule: string;
  note: string;
  interval: Interval;
}

// Все интервалы уровня выше фона со всех линий в хронологическом порядке; unknown не событие.
export function collectEvents(result: AssessmentResult): EventItem[] {
  const items: EventItem[] = [];
  for (const line of result.lines) {
    line.intervals.forEach((interval, index) => {
      if (interval.level === 'none' || interval.level === 'unknown') return;
      items.push({
        id: `${line.line}:${index}:${interval.start}`,
        line: line.line,
        category: lineLabel(line.line),
        level: interval.level,
        kind: interval.kind,
        start: interval.start,
        end: interval.end,
        rule: interval.rule,
        note: interval.note,
        interval,
      });
    });
  }
  return items.sort((a, b) => ms(a.start) - ms(b.start) || a.line.localeCompare(b.line));
}

// События сгруппированы по дате UTC в порядке возрастания.
export function groupEventsByDay(events: EventItem[]): { day: string; events: EventItem[] }[] {
  const groups = new Map<string, EventItem[]>();
  for (const event of events) {
    const day = event.start.slice(0, 10);
    if (!groups.has(day)) groups.set(day, []);
    groups.get(day)!.push(event);
  }
  return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([day, list]) => ({ day, events: list }));
}

// Ближайшие события после момента from; при отсутствии будущих берутся последние прошедшие.
export function upcomingEvents(events: EventItem[], from: number, limit = 4): EventItem[] {
  const future = events.filter((event) => ms(event.end) >= from);
  return (future.length ? future : events.slice(-limit)).slice(0, limit);
}

// Линия освещённости: тень это условие работ, не событие, поэтому в списке событий она скрыта по умолчанию.
export const SHADOW_LINE = 'illumination';

// Геометрия доступа это условный расчёт, не событие и не фактор риска: её нет в событиях и в оценке факторов.
export const GEOMETRY_LINE = 'proton_access_geometry';

export interface EventFilter {
  line?: string;
  showShadow?: boolean;
}

export function filterEvents(events: EventItem[], filter: EventFilter = {}): EventItem[] {
  return events.filter((event) => event.line !== GEOMETRY_LINE && (filter.showShadow || event.line !== SHADOW_LINE) && (!filter.line || event.line === filter.line));
}

export type PerLineHours = Record<string, Partial<Record<Level, number>>>;

export interface LineHours {
  line: string;
  label: string;
  hours: number;
}

const hoursAt = (hours: Partial<Record<Level, number>> | undefined, levels: Level[]): number => levels.reduce((acc, level) => acc + (hours?.[level] || 0), 0);

// Часы уровней по линиям окна в порядке линий; линии с нулевыми часами опущены. Часы линий не
// складываются: одна и та же минута окна считается в каждой линии отдельно.
export function hoursByLine(perLine: PerLineHours, levels: Level[]): LineHours[] {
  const names = Object.keys(perLine);
  const ordered = [...LINE_ORDER.filter((name) => names.includes(name)), ...names.filter((name) => !LINE_ORDER.includes(name as never))];
  return ordered.map((line) => ({ line, label: lineLabel(line), hours: hoursAt(perLine[line], levels) })).filter((item) => item.hours > 0);
}

// Максимум часов уровней среди линий окна; без линий 0.
export function maxHours(perLine: PerLineHours, levels: Level[]): number {
  return Object.values(perLine).reduce((best, hours) => Math.max(best, hoursAt(hours, levels)), 0);
}
