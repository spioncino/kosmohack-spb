import type { Recommendation } from '../api/client';
import { LINE_WORD, lineWord, splitClauses } from './reason';

// Тексты для человека без кодов уровней, линий и решений: объяснение рекомендации (`recommendation.explanation`)
// с пояснениями терминов и причины исключения окна (`WindowScore.excluded_reason`) обычными словами. Технический
// текст сервера (`reason`, `rules_applied`, `excluded_reason`) не меняется и показывается отдельно.

export const HOW_CHOSEN =
  'Как выбирается окно: сначала меньше опасных минут (высокий и критический уровень, протоны достают станцию), затем меньше повышенного фона и пробелов данных, при равенстве ближе к запрошенному; окно с критическим уровнем и окно без данных не предлагаются.';

export interface PlainPart {
  text: string;
  // Ключ глоссария (lib/glossary.ts), если слово открывает пояснение.
  term?: string;
}

export interface PlainSentence {
  parts: PlainPart[];
  // Предложение говорит, что поток не измерен (D64): показывается как предупреждение.
  caveat: boolean;
  // Строки по линиям («Протоны: в запрошенном окне 4 ч 42 мин, в новом 2 ч 27 мин. …») после предложения, которое
  // кончается двоеточием.
  sub: PlainSentence[];
}

export interface PlainExplanation {
  headline: PlainSentence;
  items: PlainSentence[];
  caveat: boolean;
}

// Слова объяснения, у которых есть пояснение в глоссарии; слово ищется в любом падеже и не внутри другого слова.
const WORD_START = '(?<![а-яё])';
export const PLAIN_TERMS: { term: string; pattern: RegExp }[] = [
  { term: 'danger_minutes', pattern: new RegExp(`${WORD_START}опасн[а-яё]* минут[а-яё]*`, 'iu') },
  { term: 'elevated_background', pattern: new RegExp(`${WORD_START}повышенн[а-яё]* фон[а-яё]*`, 'iu') },
  { term: 'level:elevated', pattern: new RegExp(`${WORD_START}повышенн[а-яё]* уров[а-яё]*`, 'iu') },
  { term: 'level:high', pattern: new RegExp(`${WORD_START}высок[а-яё]* уров[а-яё]*`, 'iu') },
  { term: 'level:critical', pattern: new RegExp(`${WORD_START}критическ[а-яё]* уров[а-яё]*`, 'iu') },
  { term: 'data_gap', pattern: new RegExp(`${WORD_START}пробел[а-яё]*(?: в)? данных`, 'iu') },
  { term: 'level:unknown', pattern: new RegExp(`${WORD_START}без данных`, 'iu') },
  { term: 'unmeasured', pattern: new RegExp(`${WORD_START}не измерен[а-яё]*`, 'iu') },
  { term: 'equivalent_windows', pattern: new RegExp(`${WORD_START}равн(?:означ|оцен)н[а-яё]*`, 'iu') },
];

const CAVEAT = /не измерен/iu;
// Строка по линии начинается с её названия и двоеточия: «Протоны: в запрошенном окне 18 мин, в новом нет. …».
const LINE_ROW = new RegExp(`^(?:${Object.values(LINE_WORD).join('|')}): `, 'iu');

// Предложения объяснения без пустых строк; у расчётов прежних версий поля нет, тогда список пуст.
export function explanationOf(rec: Pick<Recommendation, 'explanation'> | null | undefined): string[] {
  const list = rec?.explanation;
  if (!Array.isArray(list)) return [];
  return list.filter((s): s is string => typeof s === 'string' && s.trim() !== '').map((s) => s.trim());
}

// Предложение на части: каждое понятие получает пояснение один раз на всё объяснение, у первого вхождения.
export function termParts(text: string, used: Set<string> = new Set()): PlainPart[] {
  const parts: PlainPart[] = [];
  let rest = text;
  for (;;) {
    let found: { term: string; index: number; length: number } | null = null;
    for (const { term, pattern } of PLAIN_TERMS) {
      if (used.has(term)) continue;
      const m = pattern.exec(rest);
      if (m && (found === null || m.index < found.index)) found = { term, index: m.index, length: m[0].length };
    }
    if (!found) break;
    if (found.index > 0) parts.push({ text: rest.slice(0, found.index) });
    parts.push({ text: rest.slice(found.index, found.index + found.length), term: found.term });
    used.add(found.term);
    rest = rest.slice(found.index + found.length);
  }
  if (rest) parts.push({ text: rest });
  return parts;
}

// Объяснение для показа: первое предложение это решение (заголовок), остальные пунктами; строки по линиям после
// предложения с двоеточием в конце становятся его подпунктами. null без объяснения.
export function plainExplanation(rec: Pick<Recommendation, 'explanation'> | null | undefined): PlainExplanation | null {
  const sentences = explanationOf(rec);
  if (!sentences.length) return null;
  const used = new Set<string>();
  const [headline, ...rest] = sentences.map((text): PlainSentence => ({ parts: termParts(text, used), caveat: CAVEAT.test(text), sub: [] }));
  const items: PlainSentence[] = [];
  let lead: PlainSentence | null = null;
  for (const [i, sentence] of rest.entries()) {
    const text = sentences[i + 1];
    if (lead && LINE_ROW.test(text)) {
      lead.sub.push(sentence);
      continue;
    }
    items.push(sentence);
    lead = text.endsWith(':') ? sentence : null;
  }
  const all = [headline, ...rest];
  return { headline, items, caveat: all.some((s) => s.caveat) };
}

// Краткое объяснение для карточки «Окно для ВКД» на обзоре: решение и первые `limit` пунктов. Подпункты по линиям
// остаются при своём пункте: без них предложение с двоеточием не закончено. `more` сколько пунктов не показано,
// `caveat` о показанных предложениях. null без объяснения: карточка показывает техническую запись, как раньше.
export interface PlainBrief extends PlainExplanation {
  more: number;
}

export function plainBrief(rec: Pick<Recommendation, 'explanation'> | null | undefined, limit = 2): PlainBrief | null {
  const view = plainExplanation(rec);
  if (!view) return null;
  const items = view.items.slice(0, Math.max(0, Math.floor(limit)));
  const shown = [view.headline, ...items].flatMap((s) => [s, ...s.sub]);
  return { headline: view.headline, items, caveat: shown.some((s) => s.caveat), more: view.items.length - items.length };
}

export const plainText = (parts: PlainPart[]): string => parts.map((p) => p.text).join('');

export const minutesOf = (hours: number | null | undefined): number => Math.round((hours || 0) * 60);

// Линия в родительном падеже: «критический уровень протонов».
const LINE_GENITIVE: Record<string, string> = {
  sep_protons: 'протонов',
  meteor_showers: 'метеоров',
  conjunctions: 'сближений',
  dose_proxy: 'прокси дозы',
  illumination: 'освещённости',
  proton_access_geometry: 'геометрии доступа',
};

const AVAILABILITY_PLAIN: Record<string, string> = {
  missing: 'нет данных',
  stale: 'данные устарели',
  unusable_for_replay: 'нет архива источника',
};

// Форматы частей `excluded_reason` из src/evarisk/windows/compare.py (score, illumination_reasons).
const CRITICAL = /^critical по линии (\S+): ([\d.]+) ч$/;
const MISSING = /^недостаточно данных по линии (\S+) \((\w+)\)$/;
const GAP = /^пробел данных по линии (\S+): (\d+) мин без уровня \(D70\)$/;
const SUNLIT = /^ограничение освещённости: доля света ([\d.]+) < ([\d.]+)$/;
const SHADOW_START = 'ограничение освещённости: старт в тени';
const NO_LIGHT_DATA = 'ограничение освещённости: данных об освещённости в окне нет';

const share = (fraction: string) => `${Math.round(Number(fraction) * 100)} %`;

// Одна причина исключения обычными словами; незнакомый формат возвращается как есть.
export function plainExclusion(part: string): string {
  const text = part.trim();
  let m = CRITICAL.exec(text);
  if (m) {
    const minutes = minutesOf(Number(m[2]));
    const genitive = LINE_GENITIVE[m[1]];
    return genitive ? `критический уровень ${genitive}: ${minutes} мин` : `критический уровень, ${lineWord(m[1])}: ${minutes} мин`;
  }
  m = GAP.exec(text);
  if (m) return `пробел данных: ${lineWord(m[1])}, ${m[2]} мин`;
  m = MISSING.exec(text);
  if (m) return `${AVAILABILITY_PLAIN[m[2]] ?? 'нет данных'}: ${lineWord(m[1])}`;
  m = SUNLIT.exec(text);
  if (m) return `мало света: на свету ${share(m[1])} окна, задано не меньше ${share(m[2])}`;
  if (text === SHADOW_START) return 'старт в тени, а задан старт на свету';
  if (text === NO_LIGHT_DATA) return 'нет данных об освещённости в окне';
  return text;
}

// Все причины исключения окна: сервер соединяет их через «; ».
export function plainExclusions(reason: string | null | undefined): string[] {
  return reason ? splitClauses(reason).map(plainExclusion) : [];
}
