import type { Level, WindowScore } from '../api/client';
import { LINE_LABEL, LINE_ORDER } from './labels';
import { DECISION_CODE, decisionRoute } from './manualLinks';

// Разбор причины рекомендации (`recommendation.reason`) для показа: сервер пишет одну строку, части которой
// разделены «; ». Здесь она только перестраивается: заголовок из первой части, остальное списком, отрезки
// «04:36–04:50 sep_protons critical» отдельными метками, коды линий русскими подписями, коды решений ссылками.
// Ни одно слово причины не теряется.

export type ReasonSegment = { text: string } | { code: string; route: string };

export interface ReasonRange {
  start: string;
  end: string;
  line: string;
  lineWord: string;
  level: Level;
}

export interface ReasonItem {
  // Короткая подпись перед двоеточием («запрошенное окно хуже»), если она есть.
  label: ReasonSegment[] | null;
  body: ReasonSegment[];
  ranges: ReasonRange[];
  // «и ещё N» после перечисленных отрезков.
  more: number;
}

export interface ParsedReason {
  headline: ReasonSegment[];
  // Продолжение первой части после двоеточия, показывается под заголовком.
  note: ReasonSegment[] | null;
  // Первая часть это предупреждение о неизмеренном потоке протонов (D64).
  caveat: boolean;
  items: ReasonItem[];
}

// Короткие слова для меток отрезков: «04:36–04:50 · протоны · critical».
export const LINE_WORD: Record<string, string> = {
  sep_protons: 'протоны',
  meteor_showers: 'метеоры',
  conjunctions: 'сближения',
  dose_proxy: 'прокси дозы',
  illumination: 'освещённость',
  proton_access_geometry: 'геометрия доступа',
};

const LEVELS: Level[] = ['none', 'elevated', 'high', 'critical', 'unknown'];
const LINE_CODE = new RegExp(`\\b(${Object.keys(LINE_LABEL).join('|')})\\b`, 'g');
// Отрезок «04:36–04:50 sep_protons critical»; в расчётах прежних версий линии в отрезке нет: «04:39–04:48 critical».
const RANGE = /^(\d{2}:\d{2})–(\d{2}:\d{2})(?: (\S+))? (none|elevated|high|critical|unknown)$/;
const MORE = / и ещё (\d+)$/;
const NO_CHOICE = /^(оснований для выбора нет(?:: все \d+ окон исключены)?): (.+)$/;
// Подпись до двоеточия считается заголовком пункта, только если она короткая.
const LABEL_MAX = 60;

export const lineWord = (line: string): string => LINE_WORD[line] || LINE_LABEL[line] || line;

// Деление по «; » вне скобок: в «(… 1.50 ч у лучшего; D45, D48)» точка с запятой внутри скобок не делит причину.
export function splitClauses(reason: string): string[] {
  const parts: string[] = [];
  let depth = 0;
  let start = 0;
  for (let i = 0; i < reason.length; i++) {
    const ch = reason[i];
    if (ch === '(') depth++;
    else if (ch === ')') depth = Math.max(0, depth - 1);
    else if (ch === ';' && depth === 0 && reason[i + 1] === ' ') {
      parts.push(reason.slice(start, i));
      start = i + 2;
      i++;
    }
  }
  parts.push(reason.slice(start));
  return parts.map((p) => p.trim()).filter(Boolean);
}

// Позиция первого двоеточия-разделителя «подпись: продолжение» вне скобок; «09:28» разделителем не считается.
function colonAt(text: string): number {
  let depth = 0;
  for (let i = 0; i < text.length - 1; i++) {
    const ch = text[i];
    if (ch === '(') depth++;
    else if (ch === ')') depth = Math.max(0, depth - 1);
    else if (ch === ':' && text[i + 1] === ' ' && depth === 0) return i;
  }
  return -1;
}

// Коды линий становятся русскими подписями, коды решений отдельными сегментами со ссылкой на журнал решений.
export function segments(text: string): ReasonSegment[] {
  const readable = text.replace(LINE_CODE, (code) => LINE_LABEL[code] || code);
  const out: ReasonSegment[] = [];
  let last = 0;
  for (const m of readable.matchAll(DECISION_CODE)) {
    const at = m.index ?? 0;
    if (at > last) out.push({ text: readable.slice(last, at) });
    out.push({ code: m[0], route: decisionRoute(m[0]) });
    last = at + m[0].length;
  }
  if (last < readable.length) out.push({ text: readable.slice(last) });
  return out;
}

export function segmentsText(parts: ReasonSegment[]): string {
  return parts.map((p) => ('code' in p ? p.code : p.text)).join('');
}

// «04:36–04:50 sep_protons high, 05:24–05:33 sep_protons high и ещё 1» → отрезки и число неперечисленных;
// null, если хоть один кусок не распознан (тогда пункт показывается текстом).
export function parseRanges(text: string): { ranges: ReasonRange[]; more: number } | null {
  const moreMatch = MORE.exec(text);
  const body = moreMatch ? text.slice(0, moreMatch.index) : text;
  const ranges: ReasonRange[] = [];
  for (const piece of body.split(', ')) {
    const m = RANGE.exec(piece.trim());
    if (!m || !LEVELS.includes(m[4] as Level)) return null;
    const line = m[3] || '';
    ranges.push({ start: m[1], end: m[2], line, lineWord: line ? lineWord(line) : '', level: m[4] as Level });
  }
  return ranges.length ? { ranges, more: moreMatch ? Number(moreMatch[1]) : 0 } : null;
}

function item(clause: string): ReasonItem {
  const at = colonAt(clause);
  if (at > 0 && at <= LABEL_MAX) {
    const label = clause.slice(0, at);
    const rest = clause.slice(at + 2);
    const parsed = parseRanges(rest);
    if (parsed) return { label: segments(label), body: [], ranges: parsed.ranges, more: parsed.more };
    return { label: segments(label), body: segments(rest), ranges: [], more: 0 };
  }
  return { label: null, body: segments(clause), ranges: [], more: 0 };
}

export function parseReason(reason: string): ParsedReason {
  const clauses = splitClauses(reason);
  if (!clauses.length) return { headline: [], note: null, caveat: false, items: [] };
  const [first, ...rest] = clauses;
  const items = rest.map(item);
  const caveat = /^поток протонов не измерен/.test(first);
  // «оснований для выбора нет: все 73 окон исключены: critical по линии …»: после сводки идёт первая из
  // равноправных причин исключения, она становится первым пунктом списка.
  const none = NO_CHOICE.exec(first);
  if (none) return { headline: segments(none[1]), note: null, caveat, items: [item(none[2]), ...items] };
  const at = colonAt(first);
  if (at > 0) return { headline: segments(first.slice(0, at)), note: segments(first.slice(at + 2)), caveat, items };
  return { headline: segments(first), note: null, caveat, items };
}

// Строка окна по линии из часов `per_line`: «протоны: high+critical 72 мин, elevated 288 мин, без уровня 0 мин».
export interface LineMinutesRow {
  line: string;
  word: string;
  highCritical: number;
  elevated: number;
  unknown: number;
  text: string;
  // Та же строка обычными словами: «протоны: опасные 72 мин, повышенный фон 288 мин, без данных 0 мин».
  plainText: string;
}

const minutes = (hours: number | undefined) => Math.round((hours || 0) * 60);

export function perLineRows(perLine: WindowScore['per_line'] | null | undefined): LineMinutesRow[] {
  if (!perLine) return [];
  const names = [...LINE_ORDER.filter((n) => n in perLine), ...Object.keys(perLine).filter((n) => !LINE_ORDER.includes(n as never))];
  return names.map((line) => {
    const hours = perLine[line] || ({} as Record<Level, number>);
    const highCritical = minutes((hours.high || 0) + (hours.critical || 0));
    const elevated = minutes(hours.elevated);
    const unknown = minutes(hours.unknown);
    const word = lineWord(line);
    return {
      line,
      word,
      highCritical,
      elevated,
      unknown,
      text: `${word}: high+critical ${highCritical} мин, elevated ${elevated} мин, без уровня ${unknown} мин`,
      plainText: `${word}: опасные ${highCritical} мин, повышенный фон ${elevated} мин, без данных ${unknown} мин`,
    };
  });
}

// Оценка окна из списка кандидатов по началу окна (для предпочтительного окна рекомендации).
export function scoreOf(windows: WindowScore[], start: string | null | undefined): WindowScore | null {
  if (!start) return null;
  const at = Date.parse(start);
  return windows.find((w) => Date.parse(w.window.start) === at) || null;
}
