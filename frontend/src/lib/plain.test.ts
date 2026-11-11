import { describe, expect, it } from 'vitest';
import { GLOSSARY } from './glossary';
import { HOW_CHOSEN, PLAIN_TERMS, explanationOf, minutesOf, plainBrief, plainExclusion, plainExclusions, plainExplanation, plainText, termParts } from './plain';

// Причины исключения в форматах src/evarisk/windows/compare.py: score() и illumination_reasons().
const CRITICAL = 'critical по линии sep_protons: 0.43 ч';
const MISSING = 'недостаточно данных по линии meteor_showers (missing)';
const GAP = 'пробел данных по линии sep_protons: 225 мин без уровня (D70)';
const SUNLIT = 'ограничение освещённости: доля света 0.95 < 0.99';
const SHADOW = 'ограничение освещённости: старт в тени';
const NO_LIGHT = 'ограничение освещённости: данных об освещённости в окне нет';
// Запрошенное окно разбора 11 мая 2024 с ограничением доли света 0.99 (сохранённый расчёт сервера).
const REVIEW_REQUESTED = 'critical по линии sep_protons: 1.23 ч; ограничение освещённости: доля света 0.97 < 0.99';

const TECHNICAL = /high|critical|elevated|sep_protons|meteor_showers|conjunctions|missing|stale|D\d+|штраф/;

describe('plainExclusion', () => {
  it('переводит каждую известную причину в обычные слова', () => {
    expect(plainExclusion(CRITICAL)).toBe('критический уровень протонов: 26 мин');
    expect(plainExclusion(MISSING)).toBe('нет данных: метеоры');
    expect(plainExclusion(GAP)).toBe('пробел данных: протоны, 225 мин');
    expect(plainExclusion(SUNLIT)).toBe('мало света: на свету 95 % окна, задано не меньше 99 %');
    expect(plainExclusion(SHADOW)).toBe('старт в тени, а задан старт на свету');
    expect(plainExclusion(NO_LIGHT)).toBe('нет данных об освещённости в окне');
    for (const text of [CRITICAL, MISSING, GAP, SUNLIT, SHADOW, NO_LIGHT]) expect(plainExclusion(text)).not.toMatch(TECHNICAL);
  });

  it('называет состояние данных линии и линию', () => {
    expect(plainExclusion('недостаточно данных по линии conjunctions (unusable_for_replay)')).toBe('нет архива источника: сближения');
    expect(plainExclusion('недостаточно данных по линии sep_protons (stale)')).toBe('данные устарели: протоны');
    expect(plainExclusion('critical по линии meteor_showers: 0.25 ч')).toBe('критический уровень метеоров: 15 мин');
  });

  it('незнакомый формат показывает как есть', () => {
    expect(plainExclusion('окно вне периода поиска')).toBe('окно вне периода поиска');
    expect(plainExclusion('critical по линии sep_protons: много')).toBe('critical по линии sep_protons: много');
    expect(plainExclusion('ограничение освещённости: доля тени 0.20')).toBe('ограничение освещённости: доля тени 0.20');
  });

  it('делит составную причину по «; »', () => {
    expect(plainExclusions(REVIEW_REQUESTED)).toEqual(['критический уровень протонов: 74 мин', 'мало света: на свету 97 % окна, задано не меньше 99 %']);
    expect(plainExclusions(`${GAP}; ${MISSING}`)).toEqual(['пробел данных: протоны, 225 мин', 'нет данных: метеоры']);
    expect(plainExclusions(null)).toEqual([]);
    expect(plainExclusions('')).toEqual([]);
  });
});

// Объяснения в том виде, как их отдаёт сервер (src/evarisk/windows/explain.py, D78): первое предложение решение.
const MOVE = [
  'Рекомендуем перенести выход на 08:00–14:00 UTC, на 4 ч позже.',
  'Поток протонов не измерен на 6 ч 54 мин из 12 ч периода поиска. Там уровень взят из прогноза и сообщений о событиях.',
  'Опасных минут в обоих окнах поровну, по 12.',
  'Минут без данных в запрошенном окне 10, в новом нет.',
  'Среди окон, где опасность наименьшая, это ближайшее к запрошенному.',
  'Ещё 3 окна почти равноценны выбранному.',
];
const KEEP = ['Рекомендуем оставить запрошенное окно 13:00–19:00 UTC: перенос заметного выигрыша не даёт.'];
const NONE = ['Подходящего окна нет: исключены все 73 окна.', 'Во всех 73 окнах мало света.', 'В 38 окнах из 73 критический уровень протонов.', 'В 5 окнах из 73 пробел данных по протонам, 45 мин.'];
// Ответ сервера для разбора 10 мая 2024, запрошенное начало 13:00, период поиска 10:00–20:00.
const REVIEW_0510 = [
  'Рекомендуем перенести выход на 10:45–16:45 UTC, на 2 ч 15 мин раньше.',
  'Опасных минут нет ни в запрошенном, ни в новом окне.',
  'Зато в новом окне меньше повышенного фона:',
  'Протоны: в запрошенном окне 4 ч 42 мин, в новом 2 ч 27 мин. Протонов больше обычного, но опасная часть до экипажа не доходит: станцию закрывает магнитное поле Земли или протонов высокой энергии мало.',
  'Сближения в сравнении окон не участвуют: архива отчётов о сближениях за то время нет.',
  'Среди окон, где опасность наименьшая, это ближайшее к запрошенному.',
  'Ещё 3 окна почти равноценны выбранному.',
];
// Перенос из запрошенного окна с опасными отрезками: фраза об отрезках называет высокий и критический уровень.
const SPANS = [
  'Рекомендуем перенести выход на 09:30–17:30 UTC, на 5 ч 30 мин позже.',
  'Запрошенное окно 04:00–12:00 UTC не подходит: критический уровень протонов, всего 1 ч 14 мин.',
  'В новом окне опасных минут нет.',
  'Опасные отрезки в запрошенном окне: высокий уровень протонов 04:30–04:48; критический уровень протонов 04:48–05:00. После 05:00 UTC 11 мая опасных отрезков до конца периода поиска нет.',
];

describe('plainExplanation', () => {
  it('первое предложение заголовок, остальные пунктами, текст не меняется', () => {
    const view = plainExplanation({ explanation: MOVE })!;
    expect(plainText(view.headline.parts)).toBe(MOVE[0]);
    expect(view.items.map((item) => plainText(item.parts))).toEqual(MOVE.slice(1));
    expect(plainExplanation({ explanation: KEEP })).toEqual({ headline: { parts: [{ text: KEEP[0] }], caveat: false, sub: [] }, items: [], caveat: false });
  });

  it('открывает пояснение у первого вхождения каждого понятия', () => {
    const view = plainExplanation({ explanation: MOVE })!;
    const terms = [view.headline, ...view.items].flatMap((s) => s.parts.filter((p) => p.term).map((p) => [p.term, p.text]));
    expect(terms).toEqual([
      ['unmeasured', 'не измерен'],
      ['danger_minutes', 'Опасных минут'],
      ['level:unknown', 'без данных'],
      ['equivalent_windows', 'равноценны'],
    ]);
    const none = plainExplanation({ explanation: NONE })!;
    expect(none.items.flatMap((item) => item.parts.filter((p) => p.term))).toEqual([
      { text: 'критический уровень', term: 'level:critical' },
      { text: 'пробел данных', term: 'data_gap' },
    ]);
    const spans = plainExplanation({ explanation: SPANS })!;
    const spanTerms = spans.items.flatMap((item) => item.parts.filter((p) => p.term).map((p) => p.term));
    expect(spanTerms).toEqual(['level:critical', 'danger_minutes', 'level:high']);
  });

  it('строки по линиям после предложения с двоеточием становятся подпунктами', () => {
    const view = plainExplanation({ explanation: REVIEW_0510 })!;
    expect(plainText(view.headline.parts)).toBe(REVIEW_0510[0]);
    expect(view.items.map((item) => plainText(item.parts))).toEqual([REVIEW_0510[1], REVIEW_0510[2], REVIEW_0510[4], REVIEW_0510[5], REVIEW_0510[6]]);
    expect(view.items[1].sub.map((sub) => plainText(sub.parts))).toEqual([REVIEW_0510[3]]);
    expect(view.items[2].sub).toEqual([]);
    const terms = view.items.flatMap((item) => [item, ...item.sub]).flatMap((s) => s.parts.filter((p) => p.term).map((p) => p.term));
    expect(terms).toEqual(['danger_minutes', 'elevated_background', 'equivalent_windows']);
  });

  it('строка по линии без предложения с двоеточием остаётся пунктом', () => {
    const view = plainExplanation({ explanation: [
      'Рекомендуем оставить запрошенное окно 13:00–19:00 UTC: перенос заметного выигрыша не даёт.',
      'Опасных минут в окне 14:',
      'Протоны: 14 мин. Протоны высокой энергии достают станцию.',
      'Меньше опасных минут нет ни в одном подходящем окне.',
      'Метеоры: 1 ч. Частиц, способных пробить скафандр, больше обычного.',
      'Повышенный фон в окне:',
      'Протоны: 1 ч 30 мин. Протонов больше обычного, но опасная часть до экипажа не доходит.',
      'Сближения в сравнении окон не участвуют: архива отчётов о сближениях за то время нет.',
      'Ещё 2 окна почти равноценны выбранному.',
    ] })!;
    expect(view.items.map((item) => [plainText(item.parts), item.sub.length])).toEqual([
      ['Опасных минут в окне 14:', 1],
      ['Меньше опасных минут нет ни в одном подходящем окне.', 0],
      ['Метеоры: 1 ч. Частиц, способных пробить скафандр, больше обычного.', 0],
      ['Повышенный фон в окне:', 1],
      ['Сближения в сравнении окон не участвуют: архива отчётов о сближениях за то время нет.', 0],
      ['Ещё 2 окна почти равноценны выбранному.', 0],
    ]);
  });

  it('помечает предложение о неизмеренном потоке', () => {
    const view = plainExplanation({ explanation: MOVE })!;
    expect(view.items.map((item) => item.caveat)).toEqual([true, false, false, false, false]);
    expect(view.caveat).toBe(true);
    expect(plainExplanation({ explanation: KEEP })!.caveat).toBe(false);
    // Фраза о линиях вне сравнения окон не выдаёт себя за предупреждение о неизмеренном потоке.
    expect(plainExplanation({ explanation: REVIEW_0510 })!.caveat).toBe(false);
  });

  it('без объяснения даёт null: расчёт прежней версии показывается технической записью', () => {
    expect(plainExplanation({})).toBeNull();
    expect(plainExplanation({ explanation: [] })).toBeNull();
    expect(plainExplanation({ explanation: ['  ', ''] })).toBeNull();
    expect(plainExplanation(null)).toBeNull();
    expect(explanationOf(undefined)).toEqual([]);
    expect(explanationOf({ explanation: [' Оставить окно. ', ''] })).toEqual(['Оставить окно.']);
  });
});

describe('plainBrief', () => {
  it('решение и два следующих предложения, остальное считается', () => {
    const brief = plainBrief({ explanation: MOVE })!;
    expect(plainText(brief.headline.parts)).toBe(MOVE[0]);
    expect(brief.items.map((item) => plainText(item.parts))).toEqual(MOVE.slice(1, 3));
    expect(brief.more).toBe(3);
    expect(brief.caveat).toBe(true);
    expect(plainBrief({ explanation: MOVE }, 1)!.items.map((item) => plainText(item.parts))).toEqual([MOVE[1]]);
    expect(plainBrief({ explanation: MOVE }, 0)!).toMatchObject({ items: [], more: 5, caveat: false });
  });

  it('пункт с двоеточием показывается со строками по линиям', () => {
    const brief = plainBrief({ explanation: REVIEW_0510 })!;
    expect(brief.items.map((item) => [plainText(item.parts), item.sub.map((sub) => plainText(sub.parts))])).toEqual([
      [REVIEW_0510[1], []],
      [REVIEW_0510[2], [REVIEW_0510[3]]],
    ]);
    expect(brief.more).toBe(3);
    expect(brief.caveat).toBe(false);
  });

  it('короткое объяснение целиком, пояснения терминов как в полном', () => {
    expect(plainBrief({ explanation: KEEP })).toEqual({ ...plainExplanation({ explanation: KEEP })!, more: 0 });
    const brief = plainBrief({ explanation: NONE })!;
    expect(brief.items.map((item) => plainText(item.parts))).toEqual(NONE.slice(1, 3));
    expect(brief.items).toEqual(plainExplanation({ explanation: NONE })!.items.slice(0, 2));
    expect(brief.more).toBe(1);
  });

  it('без объяснения даёт null: карточка показывает техническую запись', () => {
    expect(plainBrief({})).toBeNull();
    expect(plainBrief({ explanation: [] })).toBeNull();
    expect(plainBrief(null)).toBeNull();
  });
});

describe('termParts', () => {
  it('ищет слово в любом падеже и не внутри другого слова', () => {
    expect(termParts('Опасных минут нет.')).toEqual([{ text: 'Опасных минут', term: 'danger_minutes' }, { text: ' нет.' }]);
    expect(termParts('Минут без данных 12.')).toEqual([{ text: 'Минут ' }, { text: 'без данных', term: 'level:unknown' }, { text: ' 12.' }]);
    expect(termParts('Неопасных минут 40.')).toEqual([{ text: 'Неопасных минут 40.' }]);
    expect(termParts('Пробел данных по протонам 30 мин.')[0]).toEqual({ text: 'Пробел данных', term: 'data_gap' });
    expect(termParts('Пробелы в данных по протонам.')[0]).toEqual({ text: 'Пробелы в данных', term: 'data_gap' });
    expect(termParts('Высокий уровень протонов 04:30–04:48.')[0]).toEqual({ text: 'Высокий уровень', term: 'level:high' });
  });

  it('уже открытое понятие второй раз не подчёркивается', () => {
    const used = new Set<string>(['danger_minutes']);
    expect(termParts('опасные минуты', used)).toEqual([{ text: 'опасные минуты' }]);
  });

  it('каждое понятие есть в глоссарии', () => {
    expect(PLAIN_TERMS.map((t) => t.term).filter((term) => !GLOSSARY[term])).toEqual([]);
  });
});

describe('обычные слова', () => {
  it('правило выбора без кодов', () => {
    expect(HOW_CHOSEN).not.toMatch(TECHNICAL);
    expect(HOW_CHOSEN.startsWith('Как выбирается окно:')).toBe(true);
  });

  it('минуты из часов', () => {
    expect(minutesOf(4.7)).toBe(282);
    expect(minutesOf(0.43)).toBe(26);
    expect(minutesOf(undefined)).toBe(0);
  });
});
