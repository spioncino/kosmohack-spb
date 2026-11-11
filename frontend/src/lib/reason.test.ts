import { describe, expect, it } from 'vitest';
import { LINE_LABEL } from './labels';
import { parseRanges, parseReason, perLineRows, scoreOf, segmentsText, splitClauses, type ParsedReason, type ReasonItem } from './reason';

// Причины рекомендаций из выгрузок docs/examples/*.zip (result.json → recommendation.reason) и из сохранённых
// расчётов сервера (оснований для выбора нет).
const REPLAY_1230 =
  'поток протонов не измерен на 1440 мин из 1440 мин периода поиска: уровень на них взят из внешнего прогноза и сообщений о событиях, это не измеренное спокойствие (D64); запрошенное окно хуже: минут high+critical 72 против 0, штраф 2.40 ч против 1.50 ч; выбрано ближайшее к запрошенному из 35 окон с минимумом таких минут (D49); равнозначными по D48 (разница до шага сетки, поэтому их может быть больше) считаются ещё 40; минуты high и critical в запрошенном окне по линиям: 04:36–04:50 sep_protons high, 05:24–05:33 sep_protons high, 06:10–06:24 sep_protons high, 06:59–07:08 sep_protons high, 07:45–07:57 sep_protons high, 08:34–08:41 sep_protons high и ещё 1; после 2024-05-11 09:28 UTC таких минут в периоде поиска нет';
const REVIEW_0511 =
  'запрошенное окно исключено; выбрано ближайшее к нему из 31 допустимых окон с минимумом минут high+critical (0), штраф 1.50 ч (D49); равнозначными по D48 (разница до шага сетки, поэтому их может быть больше) считаются ещё 34; минуты high и critical в запрошенном окне по линиям: 04:36–04:50 sep_protons critical, 05:24–05:33 sep_protons critical, 06:10–06:24 sep_protons critical, 06:59–07:08 sep_protons critical, 07:45–07:57 sep_protons critical, 08:34–08:41 sep_protons critical и ещё 1; после 2024-05-11 23:16 UTC таких минут в периоде поиска нет';
const REVIEW_0608 =
  'запрошенное окно хуже: минут high+critical 0 против 0, штраф 3.00 ч против 2.49 ч; выбрано ближайшее к запрошенному из 5 окон с минимумом таких минут (D49); равнозначными по D48 (разница до шага сетки, поэтому их может быть больше) считаются ещё 4';
const CONTROL_0613 =
  'перенос не улучшает: запрошенное окно равнозначно лучшим (минут high+critical 0 при минимуме 0, штраф 1.50 ч при 1.50 ч у лучшего; D45, D48); обстановка на периоде поиска однородна: ни одно допустимое окно не лучше запрошенного';
const DAYLIGHT_0621 =
  'запрошенное окно исключено; выбрано ближайшее к нему из 45 допустимых окон с минимумом минут high+critical (0), штраф 0.00 ч (D49); равнозначными по D48 (разница до шага сетки, поэтому их может быть больше) считаются ещё 44';
const NO_CHOICE =
  'оснований для выбора нет: все 33 окон исключены: critical по линии sep_protons в 32 окнах из 33 (0.05–0.48 ч); нет данных по линии conjunctions (missing) в 33 окнах из 33';
const NO_CHOICE_GAP = 'оснований для выбора нет: все 73 окон исключены: пробел данных по линии meteor_showers в 73 окнах из 73 (360 мин без уровня, D70)';
// Расчёт прежней версии: в отрезках нет кода линии.
const OLD_RANGES = 'запрошенное окно исключено; выбрано ближайшее к нему из 7 допустимых окон с минимумом минут high+critical (0), штраф 2.41 ч (D49); равнозначных окон 6; минуты high и critical в запрошенном окне: 04:39–04:48 critical, 06:14–06:21 critical; после 2024-05-11 06:21 UTC таких минут в периоде поиска нет';

const REAL = [REPLAY_1230, REVIEW_0511, REVIEW_0608, CONTROL_0613, DAYLIGHT_0621, NO_CHOICE, NO_CHOICE_GAP, OLD_RANGES];

// Обратная сборка строки из разбора: русские подписи линий снова становятся кодами. Совпадение с исходной
// строкой значит, что при перестройке не потеряно ни одного слова.
const CODE_BY_LABEL = new Map(Object.entries(LINE_LABEL).map(([code, label]) => [label, code]));
const unlabel = (text: string) => [...CODE_BY_LABEL].reduce((acc, [label, code]) => acc.split(label).join(code), text);
function itemText(item: ReasonItem): string {
  const label = item.label ? `${segmentsText(item.label)}: ` : '';
  const ranges = item.ranges.map((r) => `${r.start}–${r.end}${r.line ? ` ${r.line}` : ''} ${r.level}`).join(', ');
  return label + (item.ranges.length ? ranges + (item.more ? ` и ещё ${item.more}` : '') : segmentsText(item.body));
}
function rebuild(parsed: ParsedReason): string {
  const head = segmentsText(parsed.headline) + (parsed.note ? `: ${segmentsText(parsed.note)}` : '');
  const items = parsed.items.map(itemText);
  if (/^оснований для выбора нет/.test(head) && items.length) return unlabel([`${head}: ${items[0]}`, ...items.slice(1)].join('; '));
  return unlabel([head, ...items].join('; '));
}

describe('splitClauses', () => {
  it('делит по «; » вне скобок', () => {
    expect(splitClauses(CONTROL_0613)).toEqual([
      'перенос не улучшает: запрошенное окно равнозначно лучшим (минут high+critical 0 при минимуме 0, штраф 1.50 ч при 1.50 ч у лучшего; D45, D48)',
      'обстановка на периоде поиска однородна: ни одно допустимое окно не лучше запрошенного',
    ]);
    expect(splitClauses(REPLAY_1230)).toHaveLength(6);
  });
});

describe('parseReason', () => {
  it('не теряет ни одного слова настоящих причин', () => {
    for (const reason of REAL) expect(rebuild(parseReason(reason))).toBe(reason);
  });

  it('прогноз из прошлого: первая фраза о неизмеренном потоке, дальше пункты и отрезки', () => {
    const parsed = parseReason(REPLAY_1230);
    expect(parsed.caveat).toBe(true);
    expect(segmentsText(parsed.headline)).toBe('поток протонов не измерен на 1440 мин из 1440 мин периода поиска');
    expect(parsed.note?.at(-2)).toEqual({ code: 'D64', route: '/manual/DECISIONS.md#d64' });
    expect(parsed.items).toHaveLength(5);
    expect(segmentsText(parsed.items[0].label!)).toBe('запрошенное окно хуже');
    expect(segmentsText(parsed.items[0].body)).toBe('минут high+critical 72 против 0, штраф 2.40 ч против 1.50 ч');
    expect(parsed.items[1].body).toContainEqual({ code: 'D49', route: '/manual/DECISIONS.md#d49' });
    expect(parsed.items[2].body[1]).toEqual({ code: 'D48', route: '/manual/DECISIONS.md#d48' });
    const ranges = parsed.items[3];
    expect(segmentsText(ranges.label!)).toBe('минуты high и critical в запрошенном окне по линиям');
    expect(ranges.ranges).toHaveLength(6);
    expect(ranges.ranges[0]).toEqual({ start: '04:36', end: '04:50', line: 'sep_protons', lineWord: 'протоны', level: 'high' });
    expect(ranges.more).toBe(1);
    expect(segmentsText(parsed.items[4].body)).toBe('после 2024-05-11 09:28 UTC таких минут в периоде поиска нет');
  });

  it('разбор 11 мая: короткий заголовок, отрезки critical', () => {
    const parsed = parseReason(REVIEW_0511);
    expect(parsed.caveat).toBe(false);
    expect(segmentsText(parsed.headline)).toBe('запрошенное окно исключено');
    expect(parsed.note).toBeNull();
    expect(parsed.items[2].ranges.map((r) => r.level)).toEqual(Array(6).fill('critical'));
  });

  it('перенос не улучшает: продолжение под заголовком с кодами D45 и D48', () => {
    const parsed = parseReason(CONTROL_0613);
    expect(segmentsText(parsed.headline)).toBe('перенос не улучшает');
    expect(parsed.note?.filter((s) => 'code' in s).map((s) => ('code' in s ? s.code : ''))).toEqual(['D45', 'D48']);
    expect(segmentsText(parsed.items[0].label!)).toBe('обстановка на периоде поиска однородна');
  });

  it('оснований для выбора нет: причины исключения списком, коды линий по-русски', () => {
    const parsed = parseReason(NO_CHOICE);
    expect(segmentsText(parsed.headline)).toBe('оснований для выбора нет: все 33 окон исключены');
    expect(parsed.items.map((i) => segmentsText(i.body))).toEqual([
      'critical по линии Протоны СЭЧ в 32 окнах из 33 (0.05–0.48 ч)',
      'нет данных по линии Сближения (missing) в 33 окнах из 33',
    ]);
    const gap = parseReason(NO_CHOICE_GAP);
    expect(segmentsText(gap.items[0].body)).toBe('пробел данных по линии Метеорные потоки в 73 окнах из 73 (360 мин без уровня, D70)');
    expect(gap.items[0].body).toContainEqual({ code: 'D70', route: '/manual/DECISIONS.md#d70' });
  });

  it('отрезки прежней версии без кода линии', () => {
    const parsed = parseReason(OLD_RANGES);
    expect(parsed.items[2].ranges).toEqual([
      { start: '04:39', end: '04:48', line: '', lineWord: '', level: 'critical' },
      { start: '06:14', end: '06:21', line: '', lineWord: '', level: 'critical' },
    ]);
  });

  it('нераспознанный перечень остаётся текстом', () => {
    expect(parseRanges('04:36–04:50 sep_protons high, что-то другое')).toBeNull();
    expect(parseReason('').items).toEqual([]);
  });
});

describe('perLineRows', () => {
  // requested_window.per_line прогноза из прошлого 11 мая 2024 (docs/examples/replay_2024-05-11_cutoff_05-10T1230.zip).
  const perLine = {
    meteor_showers: { none: 6.0, elevated: 0.0, high: 0.0, critical: 0.0, unknown: 0.0 },
    sep_protons: { none: 0.0, elevated: 4.8, high: 1.2000000000000002, critical: 0.0, unknown: 0.0 },
  };

  it('даёт строку по каждой линии в порядке линий', () => {
    expect(perLineRows(perLine).map((r) => r.text)).toEqual([
      'протоны: high+critical 72 мин, elevated 288 мин, без уровня 0 мин',
      'метеоры: high+critical 0 мин, elevated 0 мин, без уровня 0 мин',
    ]);
    expect(perLineRows(null)).toEqual([]);
  });

  it('даёт ту же строку обычными словами', () => {
    expect(perLineRows(perLine).map((r) => r.plainText)).toEqual([
      'протоны: опасные 72 мин, повышенный фон 288 мин, без данных 0 мин',
      'метеоры: опасные 0 мин, повышенный фон 0 мин, без данных 0 мин',
    ]);
  });

  it('находит оценку окна по началу', () => {
    const windows = [{ window: { start: '2024-05-11T09:30:00Z', end: '2024-05-11T15:30:00Z' }, per_line: perLine, completeness: 1, excluded_reason: null }];
    expect(scoreOf(windows, '2024-05-11T09:30:00+00:00')).toBe(windows[0]);
    expect(scoreOf(windows, '2024-05-11T09:45:00Z')).toBeNull();
    expect(scoreOf(windows, null)).toBeNull();
  });
});
