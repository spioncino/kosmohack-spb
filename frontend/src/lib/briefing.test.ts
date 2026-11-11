import { describe, expect, it } from 'vitest';
import type { Briefing, BriefingFacts } from '../api/client';
import { askTurns, CHIP_MODEL, CHIP_TEMPLATE, factRows, fallbackReason, sourceChip, speechMode, subtitle } from './briefing';

const provenance = [{ raw_id: 'r1', source: 'swpc_archive', product: '3day_forecast', published_at: '2024-05-10T12:30:00Z', published_at_kind: 'exact' as const, data_version: 'v1', url: 'https://example' }];

// Факты соответствуют разбору 10 мая 2024 из фикстуры: запрошенное 14:00–20:00, рекомендовано 13:00–19:00,
// один отрезок high 17:18–17:58 по линии протонов, после 17:58 таких минут в периоде нет.
const facts: BriefingFacts = {
  assessment_id: 'a1',
  question: 'summary',
  mode: 'review',
  data_moment: null,
  cutoff: null,
  basis: 'разбор по архиву, не прогноз: использованы все опубликованные данные',
  verdict: 'move_to',
  requested: { start: '2024-05-10T14:00:00Z', end: '2024-05-10T20:00:00Z', duration_hours: 6, exposure_minutes: 40, unknown_minutes: 0 },
  recommended: { start: '2024-05-10T13:00:00Z', end: '2024-05-10T19:00:00Z', duration_hours: 6, exposure_minutes: 40, unknown_minutes: 0 },
  exposed: [{ start: '2024-05-10T17:18:00Z', end: '2024-05-10T17:58:00Z', line: 'sep_protons', level: 'high', minutes: 40 }],
  exposed_total: 1,
  clear_after: '2024-05-10T17:58:00Z',
  gaps: [],
  disabled_sources: [],
  confidence_grade: 'medium',
  confidence_depends_on: 'коэффициент Kp-поправки оценён по рисунку статьи',
  marginal_note: null,
  windows_total: 13,
  windows_excluded: 0,
  provenance,
  algorithm_version: 'v',
  allowed_values: ['14:00', '20:00', '40'],
};

function briefing(patch: Partial<Briefing> = {}, factsPatch: Partial<BriefingFacts> = {}): Briefing {
  return {
    assessment_id: 'a1',
    question: 'summary',
    language: 'ru',
    text: 'Перенос. Решение за руководителем работ.',
    speech_text: 'Перенос. Решение за руководителем работ.',
    source: 'llm',
    model: 'Qwen 3.8',
    validated: true,
    violations: [],
    facts: { ...facts, ...factsPatch },
    generated_at: '2026-09-19T10:00:00Z',
    algorithm_version: 'v',
    audio_url: null,
    voice_error: null,
    ...patch,
  };
}

describe('происхождение текста сводки', () => {
  it('текст модели помечен как проверенный по числам, модель названа в подсказке', () => {
    const chip = sourceChip(briefing());
    expect(chip.text).toBe(CHIP_MODEL);
    expect(chip.tone).toBe('model');
    expect(chip.title).toContain('Qwen 3.8');
  });

  it('шаблон помечен как шаблон и без модели причины не показывает', () => {
    const template = briefing({ source: 'template', model: null });
    expect(sourceChip(template).text).toBe(CHIP_TEMPLATE);
    expect(sourceChip(template).tone).toBe('template');
    expect(fallbackReason(template)).toBeNull();
  });

  it('шаблон после отказа проверки называет нарушения', () => {
    const rejected = briefing({ source: 'template', violations: ['число 55 в фактах не встречается'] });
    expect(fallbackReason(rejected)).toBe('Текст модели не принят: число 55 в фактах не встречается');
    expect(fallbackReason(briefing({ violations: ['число 55 в фактах не встречается'] }))).toBeNull();
  });
});

describe('подпись карточки называет основание данных', () => {
  it('разбор по архиву не выдаётся за прогноз', () => {
    expect(subtitle(briefing())).toBe('разбор по архиву, не прогноз');
  });

  it('прогноз из прошлого называет отсечение', () => {
    expect(subtitle(briefing({}, { mode: 'replay', cutoff: '2024-05-10T12:30:00Z' }))).toBe('по данным на 2024-05-10 12:30 UTC');
  });

  it('режим «сейчас» называет момент данных, а без него момент запроса', () => {
    expect(subtitle(briefing({}, { mode: 'current', data_moment: '2026-09-19T09:00:00Z' }))).toBe('данные на 2026-09-19 09:00 UTC');
    expect(subtitle(briefing({}, { mode: 'current', data_moment: null }))).toBe('данные на момент запроса');
  });
});

describe('выбор способа озвучки', () => {
  it('без тумблера звука нет', () => {
    expect(speechMode(briefing(), false, true)).toBe('off');
    expect(speechMode(null, true, true)).toBe('off');
  });

  it('файл сервера имеет приоритет, иначе речь браузера, иначе прямо сказано о неподдержке', () => {
    expect(speechMode(briefing({ audio_url: '/assessments/a1/briefing/audio?question=summary&language=ru' }), true, true)).toBe('audio');
    expect(speechMode(briefing(), true, true)).toBe('browser');
    expect(speechMode(briefing(), true, false)).toBe('unsupported');
  });
});

describe('список фактов под текстом', () => {
  const rows = factRows(briefing());

  it('первым идёт основание данных, затем окна с минутами уровня', () => {
    expect(rows[0]).toEqual({ label: 'Основание данных', value: facts.basis, rawIds: [] });
    expect(rows[1].label).toBe('Запрошенное окно');
    expect(rows[1].value).toContain('2024-05-10 14:00 UTC');
    expect(rows[1].value).toContain('высокий и критический уровень 40 мин');
    expect(rows[1].rawIds).toEqual(['r1']);
    expect(rows[2].label).toBe('Рекомендованное окно');
  });

  it('отрезок уровня назван линией и длиной, дальше момент чистого времени', () => {
    expect(rows[3].label).toBe('Отрезок высокий');
    expect(rows[3].value).toContain('Протоны СЭЧ');
    expect(rows[3].value).toContain('40 мин');
    expect(rows[4]).toEqual({ label: 'После этого момента таких минут нет', value: '2024-05-10 17:58 UTC', rawIds: [] });
  });

  it('пробелы данных, отключённые источники и счёт окон попадают в список', () => {
    const gapped = factRows(briefing({}, { gaps: [{ line: 'sep_protons', availability: 'missing' }], disabled_sources: ['goes_sgps'], windows_excluded: 13 }));
    expect(gapped.some((row) => row.label === 'Пробел данных' && row.value === 'Протоны СЭЧ: нет данных')).toBe(true);
    expect(gapped.some((row) => row.label === 'Источники отключены' && row.value === 'goes_sgps')).toBe(true);
    expect(gapped[gapped.length - 1]).toEqual({ label: 'Окна', value: 'исключено 13 из 13', rawIds: [] });
  });

  it('минуты без данных это «не определена», а не ноль', () => {
    const empty = factRows(briefing({}, { requested: { ...facts.requested, exposure_minutes: null, unknown_minutes: null } }));
    expect(empty[1].value).toContain('высокий и критический уровень не определена');
  });

  // Сводки после D84: минуты по линиям вместо суммы. Сумма 104 мин по двум линиям больше части окна, где линии
  // одновременно дают высокий уровень, поэтому в списке фактов её нет.
  const perLine = {
    requested: { ...facts.requested, exposure_minutes: 104, unknown_minutes: 12, exposure_by_line: { meteor_showers: 30, sep_protons: 74 }, unknown_by_line: { sep_protons: 12, meteor_showers: 0 }, elevated_by_line: { sep_protons: 0, meteor_showers: 0 } },
    recommended: { ...facts.recommended!, exposure_minutes: 0, exposure_by_line: { sep_protons: 0, meteor_showers: 0 } },
  };

  it('минуты высокого и критического уровня и без уровня названы по каждой линии, сумма по линиям не называется', () => {
    const lined = factRows(briefing({}, perLine));
    expect(lined[1].value).toContain('высокий и критический уровень: протоны 74 мин, метеорные потоки 30 мин');
    expect(lined[1].value).toContain('без уровня: протоны 12 мин, метеорные потоки 0 мин');
    expect(lined[1].value).not.toContain('104');
    expect(lined[2].value).toContain('высокий и критический уровень: протоны 0 мин, метеорные потоки 0 мин');
  });

  it('у сводок без минут по линиям или с пустыми полями остаётся прежняя сумма', () => {
    expect(rows[1].value).toContain('высокий и критический уровень 40 мин · без уровня 0 мин');
    const emptyMaps = factRows(briefing({}, { requested: { ...facts.requested, exposure_by_line: {}, unknown_by_line: {} } }));
    expect(emptyMaps[1].value).toContain('высокий и критический уровень 40 мин · без уровня 0 мин');
  });
});

describe('askTurns', () => {
  it('берёт до трёх последних ответов модели от старого к новому', () => {
    // История хранится от нового к старому, как в карточке сводки.
    const history = [
      { asked: 'а если в 08:00?', text: 'В окне с 08:00 опасных минут по протонам 14.', source: 'llm' as const },
      { asked: 'когда тень?', text: 'На этот вопрос проверенного ответа нет.', source: 'template' as const },
      { asked: 'почему 09:30?', text: 'В запрошенном окне опасных минут 72.', source: 'llm' as const },
      { asked: 'можно доверять?', text: 'Уверенность в оценке протонов низкая.', source: 'llm' as const },
      { asked: 'самый старый', text: 'Не попадёт.', source: 'llm' as const },
    ];
    expect(askTurns(history)).toEqual([
      { asked: 'можно доверять?', answer: 'Уверенность в оценке протонов низкая.' },
      { asked: 'почему 09:30?', answer: 'В запрошенном окне опасных минут 72.' },
      { asked: 'а если в 08:00?', answer: 'В окне с 08:00 опасных минут по протонам 14.' },
    ]);
    expect(askTurns([])).toEqual([]);
  });
});
