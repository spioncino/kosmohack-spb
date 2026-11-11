import { describe, expect, it } from 'vitest';
import type { AssessmentResult, Interval, LineAssessment } from '../api/client';
import { badgeColor, collectEvents, filterEvents, fmtMinutes, groupEventsByDay, hasUnmeasured, hoursByLine, lineScore, lineScores, maxHours, periodPercent, requestedSpan, scoresByLine, unmeasuredLabel, unmeasuredNote, upcomingEvents, windowLabel } from './score';

const provenance = [{ raw_id: 'r1', source: 'goes_sgps', product: 'avg5m', published_at: '2024-05-11T00:05:00Z', published_at_kind: 'derived' as const, data_version: 'v3', url: 'https://example' }];

function interval(start: string, end: string, level: Interval['level'], kind: Interval['kind'] = 'computed'): Interval {
  return { start, end, level, kind, provenance, rule: 'правило', note: '', start_uncertainty: null, end_uncertainty: null };
}

function line(name: string, intervals: Interval[], availability: LineAssessment['availability'] = 'available'): LineAssessment {
  return { line: name, mechanism: 'm', intervals, availability, confidence: { grade: 'medium', depends_on: 'x' }, limitations: [], chain_ids: [], metrics: {} };
}

// Период поиска 11 мая 2024 00:00–14:00 (14 ч = 840 мин), как в примерах docs/examples.
const SEARCH_START = '2024-05-11T00:00:00Z';
const SEARCH_END = '2024-05-11T14:00:00Z';

describe('оценка линии в процентах', () => {
  it('доля минут периода без high и critical', () => {
    // critical 04:39–07:52 = 193 мин из 840: без high/critical 647/840 = 77.0 %.
    const sep = line('sep_protons', [interval(SEARCH_START, '2024-05-11T04:39:00Z', 'none'), interval('2024-05-11T04:39:00Z', '2024-05-11T07:52:00Z', 'critical'), interval('2024-05-11T07:52:00Z', SEARCH_END, 'none')]);
    const score = lineScore(sep, SEARCH_START, SEARCH_END);
    expect(score.totalMinutes).toBe(840);
    expect(score.badMinutes).toBe(193);
    expect(score.percent).toBeCloseTo(77.02, 1);
    expect(score.completeness).toBe(1);
    // Без запрошенного окна цвет не зелёный: окно не определено.
    expect(score.window).toBeNull();
    expect(score.windowDangerMinutes).toBeNull();
    expect(score.color).toBe('yellow');
    expect(score.reason).toBe('запрошенное окно не определено');
  });

  it('интервалы за пределами периода поиска обрезаются', () => {
    const sep = line('sep_protons', [interval('2024-05-10T22:00:00Z', '2024-05-11T02:00:00Z', 'high'), interval('2024-05-11T02:00:00Z', SEARCH_END, 'none')]);
    expect(lineScore(sep, SEARCH_START, SEARCH_END).badMinutes).toBe(120);
  });

  it('линия без данных даёт null и серую плашку, а не зелёную', () => {
    const missing = line('dose_proxy', [], 'missing');
    const score = lineScore(missing, SEARCH_START, SEARCH_END);
    expect(score.percent).toBeNull();
    expect(score.color).toBe('gray');
    expect(lineScore(line('conjunctions', [], 'unusable_for_replay'), SEARCH_START, SEARCH_END).color).toBe('gray');
  });

  it('unknown в периоде поиска снижает полноту и плашка не зелёная даже при 100 % и окне только none', () => {
    const partial = line('meteor_showers', [interval(SEARCH_START, '2024-05-11T07:00:00Z', 'none'), interval('2024-05-11T07:00:00Z', SEARCH_END, 'unknown')]);
    const score = lineScore(partial, SEARCH_START, SEARCH_END, { start: '2024-05-11T01:00:00Z', end: '2024-05-11T07:00:00Z' });
    expect(score.percent).toBe(100);
    expect(score.completeness).toBeCloseTo(0.5);
    expect(score.windowMinutes).toEqual({ none: 360, elevated: 0, high: 0, critical: 0, unknown: 0 });
    expect(score.color).toBe('yellow');
    expect(score.reason).toBe('полнота периода поиска 50 %');
  });
});

describe('цвет плашки по запрошенному окну', () => {
  const minutes = (m: Partial<Record<'none' | 'elevated' | 'high' | 'critical' | 'unknown', number>>) => ({ none: 0, elevated: 0, high: 0, critical: 0, unknown: 0, ...m });

  it('high или critical в окне дают красную при любом проценте периода', () => {
    expect(badgeColor(99, minutes({ none: 286, critical: 74 }), true)).toBe('red');
    expect(badgeColor(99, minutes({ none: 359, high: 1 }), true)).toBe('red');
    // Неполные данные не смягчают красную.
    expect(badgeColor(99, minutes({ unknown: 300, high: 60 }), false)).toBe('red');
  });

  it('elevated, unknown, неполные данные или неизвестное окно дают жёлтую', () => {
    expect(badgeColor(100, minutes({ none: 300, elevated: 60 }), true)).toBe('yellow');
    expect(badgeColor(100, minutes({ none: 300, unknown: 60 }), true)).toBe('yellow');
    expect(badgeColor(100, minutes({ none: 360 }), false)).toBe('yellow');
    expect(badgeColor(100, null, true)).toBe('yellow');
  });

  it('зелёная только при одном none в окне и полных данных; без данных серая', () => {
    expect(badgeColor(35, minutes({ none: 360 }), true)).toBe('green');
    expect(badgeColor(null, minutes({ critical: 360 }), true)).toBe('gray');
    expect(badgeColor(null, null, false)).toBe('gray');
  });

  it('погрешность сложения дробных часов не считается уровнем в окне', () => {
    expect(badgeColor(100, minutes({ none: 360, high: 1e-9 }), true)).toBe('green');
    expect(fmtMinutes(74.00000000000001)).toBe('74');
    expect(fmtMinutes(0.2)).toBe('<1');
    expect(fmtMinutes(0)).toBe('0');
  });
});

// Разбор 11 мая 2024 (docs/examples/review_2024-05-11.zip): период поиска сутки 00:00–24:00, запрошенное окно
// 04:00–10:00. Интервалы протонной линии и часы requested_window.per_line взяты из result.json примера.
describe('разбор 11 мая 2024: цвет по запрошенному окну', () => {
  const DAY_START = '2024-05-11T00:00:00Z';
  const DAY_END = '2024-05-12T00:00:00Z';
  const t = (hhmm: string) => (hhmm === '24:00' ? DAY_END : `2024-05-11T${hhmm}:00Z`);
  const seq = (points: [string, Interval['level']][], end: string): Interval[] => points.map(([start, level], i) => interval(t(start), t(i + 1 < points.length ? points[i + 1][0] : end), level));

  const protons = line('sep_protons', seq([
    ['00:00', 'elevated'], ['00:58', 'none'], ['01:03', 'elevated'], ['01:08', 'none'], ['02:13', 'elevated'], ['02:18', 'critical'],
    ['02:22', 'elevated'], ['03:02', 'critical'], ['03:16', 'elevated'], ['03:50', 'critical'], ['03:58', 'elevated'], ['04:36', 'critical'],
    ['04:50', 'elevated'], ['05:24', 'critical'], ['05:33', 'elevated'], ['06:10', 'critical'], ['06:24', 'elevated'], ['06:59', 'critical'],
    ['07:08', 'elevated'], ['07:45', 'critical'], ['07:57', 'elevated'], ['08:34', 'critical'], ['08:41', 'elevated'], ['09:20', 'critical'],
    ['09:29', 'elevated'], ['23:09', 'high'], ['23:16', 'elevated'],
  ], '24:00'));
  const meteors = line('meteor_showers', [interval(DAY_START, DAY_END, 'none', 'external_forecast')]);
  // Прокси дозы повторяет проходы критических минут протонов уровнем elevated; в per_line его нет.
  const dose = line('dose_proxy', seq([
    ['00:00', 'none'], ['04:36', 'elevated'], ['04:50', 'none'], ['05:24', 'elevated'], ['05:33', 'none'], ['06:10', 'elevated'], ['06:24', 'none'],
    ['06:59', 'elevated'], ['07:08', 'none'], ['07:45', 'elevated'], ['07:57', 'none'], ['08:34', 'elevated'], ['08:41', 'none'],
    ['09:20', 'elevated'], ['09:29', 'none'],
  ], '24:00'));
  const geometry = line('proton_access_geometry', [interval(DAY_START, DAY_END, 'none')]);
  const conjunctions = line('conjunctions', [], 'unusable_for_replay');

  function review(perLine: Record<string, Record<Interval['level'], number>>, lines = [protons, geometry, meteors, conjunctions, dose]): AssessmentResult {
    const base = result(lines);
    return {
      ...base,
      request: { ...base.request, search_start: DAY_START, search_end: DAY_END, eva_start: '2024-05-11T04:00:00Z', duration_hours: 6 },
      requested_window: { window: { start: '2024-05-11T04:00:00Z', end: '2024-05-11T10:00:00Z' }, per_line: perLine, completeness: 1, excluded_reason: 'critical по линии sep_protons: 1.23 ч' },
    };
  }
  const PER_LINE = {
    sep_protons: { none: 0, elevated: 4.766666666666667, high: 0, critical: 1.2333333333333334, unknown: 0 },
    meteor_showers: { none: 6, elevated: 0, high: 0, critical: 0, unknown: 0 },
  };

  it('протоны: 92 % периода без high/critical, но 74 мин critical в окне дают красную', () => {
    const score = scoresByLine(lineScores(review(PER_LINE))).get('sep_protons')!;
    expect(score.percent).toBeCloseTo(92.57, 1);
    expect(score.windowSource).toBe('per_line');
    expect(score.windowDangerMinutes).toBeCloseTo(74, 6);
    expect(score.color).toBe('red');
    expect(score.reason).toBe('в запрошенном окне critical 74 мин');
    expect(periodPercent(score)).toBe(92);
    expect(windowLabel(score)).toBe('в запрошенном окне: 74 мин high+critical');
  });

  it('без per_line минуты окна считаются по интервалам eva_start + duration с тем же итогом', () => {
    const score = scoresByLine(lineScores(review({}))).get('sep_protons')!;
    expect(score.windowSource).toBe('intervals');
    expect(score.window).toEqual({ start: '2024-05-11T04:00:00Z', end: '2024-05-11T10:00:00Z' });
    expect(score.windowMinutes).toEqual({ none: 0, elevated: 286, high: 0, critical: 74, unknown: 0 });
    expect(score.color).toBe('red');
  });

  it('метеоры: в окне только none при полных данных дают зелёную', () => {
    const score = scoresByLine(lineScores(review(PER_LINE))).get('meteor_showers')!;
    expect(score.percent).toBe(100);
    expect(score.windowDangerMinutes).toBe(0);
    expect(score.color).toBe('green');
    expect(score.reason).toBe('в запрошенном окне только none, данные полные');
    expect(windowLabel(score)).toBe('в запрошенном окне: 0 мин high+critical');
  });

  it('линия с минутами unknown в окне не зелёная', () => {
    const perLine = { ...PER_LINE, meteor_showers: { none: 5, elevated: 0, high: 0, critical: 0, unknown: 1 } };
    const score = scoresByLine(lineScores(review(perLine))).get('meteor_showers')!;
    expect(score.color).toBe('yellow');
    expect(score.reason).toBe('в запрошенном окне unknown 60 мин');
    // Тот же случай по интервалам: unknown 07:00–08:00 внутри окна.
    const gap = line('meteor_showers', [interval(DAY_START, t('07:00'), 'none'), interval(t('07:00'), t('08:00'), 'unknown'), interval(t('08:00'), DAY_END, 'none')]);
    const byIntervals = scoresByLine(lineScores(review({}, [protons, gap]))).get('meteor_showers')!;
    expect(byIntervals.windowMinutes!.unknown).toBe(60);
    expect(byIntervals.color).not.toBe('green');
  });

  it('часть окна без интервалов даёт жёлтую, а не зелёную', () => {
    const short = line('meteor_showers', [interval(DAY_START, t('08:00'), 'none')]);
    const score = scoresByLine(lineScores(review({}, [short]))).get('meteor_showers')!;
    expect(score.windowUncoveredMinutes).toBe(120);
    expect(score.color).toBe('yellow');
    expect(score.reason).toBe('120 мин окна без интервалов; полнота периода поиска 33 %');
  });

  it('прокси дозы вне per_line: elevated в окне по интервалам даёт жёлтую при 0 мин high+critical', () => {
    const score = scoresByLine(lineScores(review(PER_LINE))).get('dose_proxy')!;
    expect(score.windowSource).toBe('intervals');
    expect(score.windowMinutes!.elevated).toBe(74);
    expect(score.windowDangerMinutes).toBe(0);
    expect(score.color).toBe('yellow');
  });

  it('линия без архива источника серая и подписана «нет архива за эти даты», а не «нет данных»', () => {
    const score = scoresByLine(lineScores(review({ ...PER_LINE, conjunctions: { none: 0, elevated: 0, high: 0, critical: 6, unknown: 0 } }))).get('conjunctions')!;
    expect(score.color).toBe('gray');
    expect(score.windowDangerMinutes).toBeNull();
    expect(windowLabel(score)).toBe('нет архива за эти даты');
    expect(score.reason).toContain('SOCRATES публикует только текущий отчёт на 7 суток вперёд');
    expect(periodPercent(score)).toBeNull();
  });

  it('линия без данных остаётся серой «нет данных»', () => {
    const missing = line('dose_proxy', [], 'missing');
    const score = scoresByLine(lineScores(review(PER_LINE, [protons, geometry, meteors, conjunctions, missing]))).get('dose_proxy')!;
    expect(score.color).toBe('gray');
    expect(windowLabel(score)).toBe('нет данных');
    expect(score.reason).toBe('данных по линии нет');
  });

  it('оценки ищутся по имени линии: у геометрии доступа оценки нет, соседние вкладки не сдвигаются', () => {
    const scores = lineScores(review(PER_LINE));
    // Порядок вкладок на экране рисков: протоны, геометрия, метеоры, сближения, доза.
    expect(scores.map((s) => s.line)).toEqual(['sep_protons', 'meteor_showers', 'conjunctions', 'dose_proxy']);
    const byLine = scoresByLine(scores);
    expect(byLine.get('proton_access_geometry')).toBeUndefined();
    expect(windowLabel(byLine.get('proton_access_geometry'))).toBe('без оценки');
    expect(byLine.get('meteor_showers')!.color).toBe('green');
    expect(byLine.get('conjunctions')!.color).toBe('gray');
  });

  it('без requested_window окно берётся из eva_start + duration_hours', () => {
    const base = review(PER_LINE);
    const legacy = { ...base, requested_window: undefined } as unknown as AssessmentResult;
    expect(requestedSpan(legacy, 'sep_protons')).toEqual({ start: '2024-05-11T04:00:00Z', end: '2024-05-11T10:00:00Z', hours: null });
    expect(requestedSpan(base, 'sep_protons')!.hours).toEqual(PER_LINE.sep_protons);
    expect(requestedSpan({ ...legacy, request: { ...legacy.request, duration_hours: 0 } }, 'sep_protons')).toBeNull();
  });

  it('процент для показа округляется вниз', () => {
    const score = scoresByLine(lineScores(review(PER_LINE))).get('sep_protons')!;
    expect(periodPercent(score)).toBe(92);
    expect(periodPercent({ ...score, percent: 99.7 })).toBe(99);
    expect(periodPercent(null)).toBeNull();
  });
});

function result(lines: LineAssessment[]): AssessmentResult {
  return {
    id: 'a', created_at: SEARCH_START, asof: null, algorithm_version: 'v',
    request: { mode: 'review', cutoff: null, eva_start: '2024-05-11T04:00:00Z', duration_hours: 6, search_start: SEARCH_START, search_end: SEARCH_END },
    trajectory: { provider: 'oem', epoch: SEARCH_START, is_reconstruction: true, provenance: provenance[0], age_at_query_hours: null, samples: [] },
    lines,
    requested_window: { window: { start: '2024-05-11T04:00:00Z', end: '2024-05-11T10:00:00Z' }, per_line: {}, completeness: 1, excluded_reason: null },
    windows: [], recommendation: { preferred: null, reason: '', equivalents: [], insufficient: true, rules_applied: [] },
    sources_used: [], data_availability: {}, limitations: [], rules: [], verification: null,
  };
}

describe('события из интервалов', () => {
  const sep = line('sep_protons', [interval(SEARCH_START, '2024-05-11T04:39:00Z', 'none'), interval('2024-05-11T04:39:00Z', '2024-05-11T07:52:00Z', 'critical', 'observation')]);
  const meteors = line('meteor_showers', [interval('2024-05-11T01:00:00Z', '2024-05-11T01:51:00Z', 'elevated', 'external_forecast'), interval('2024-05-11T09:00:00Z', SEARCH_END, 'unknown')]);
  const shadow = line('illumination', [interval('2024-05-12T00:10:00Z', '2024-05-12T00:16:00Z', 'elevated')]);

  it('берёт только уровни выше фона со всех линий и сортирует по времени', () => {
    const events = collectEvents(result([sep, meteors, shadow]));
    expect(events.map((e) => [e.line, e.level])).toEqual([['meteor_showers', 'elevated'], ['sep_protons', 'critical'], ['illumination', 'elevated']]);
    expect(events[1].kind).toBe('observation');
    expect(events[1].category).toBe('Протоны СЭЧ');
  });

  it('группирует по дате UTC и выбирает ближайшие после момента', () => {
    const events = collectEvents(result([sep, meteors, shadow]));
    const groups = groupEventsByDay(events);
    expect(groups.map((g) => [g.day, g.events.length])).toEqual([['2024-05-11', 2], ['2024-05-12', 1]]);
    expect(upcomingEvents(events, Date.UTC(2024, 4, 11, 5), 4).map((e) => e.line)).toEqual(['sep_protons', 'illumination']);
    expect(upcomingEvents(events, Date.UTC(2024, 4, 13), 2).map((e) => e.line)).toEqual(['sep_protons', 'illumination']);
  });

  it('оценки по линиям идут в порядке API', () => {
    const scores = lineScores(result([meteors, sep]));
    expect(scores.map((s) => s.line)).toEqual(['sep_protons', 'meteor_showers']);
  });

  it('тень скрыта по умолчанию и показывается по переключателю; фильтр по линии сохраняется', () => {
    const events = collectEvents(result([sep, meteors, shadow]));
    expect(filterEvents(events).map((e) => e.line)).toEqual(['meteor_showers', 'sep_protons']);
    expect(filterEvents(events, { showShadow: true }).map((e) => e.line)).toEqual(['meteor_showers', 'sep_protons', 'illumination']);
    expect(filterEvents(events, { line: 'sep_protons' }).map((e) => e.line)).toEqual(['sep_protons']);
    expect(filterEvents(events, { line: 'illumination' })).toEqual([]);
    expect(filterEvents(events, { line: 'illumination', showShadow: true }).map((e) => e.line)).toEqual(['illumination']);
  });
});

describe('часы окна по линиям', () => {
  // Запрошенное окно replay 11 мая 2024 04:00–10:00: протоны elevated 6 ч по внешнему прогнозу, метеоры
  // elevated 3.65 ч; сумма 9.65 ч превышает длину окна 6 ч, итог это максимум по линиям.
  const perLine = {
    sep_protons: { none: 0, elevated: 6, high: 0, critical: 0, unknown: 0 },
    meteor_showers: { none: 2.35, elevated: 3.65, high: 0, critical: 0, unknown: 0 },
  };

  it('максимум по линиям не превышает длину окна', () => {
    expect(maxHours(perLine, ['elevated'])).toBe(6);
    expect(maxHours(perLine, ['high', 'critical'])).toBe(0);
    expect(maxHours({}, ['elevated'])).toBe(0);
  });

  it('список по линиям содержит только ненулевые часы в порядке линий с подписями', () => {
    expect(hoursByLine(perLine, ['elevated'])).toEqual([
      { line: 'sep_protons', label: 'Протоны СЭЧ', hours: 6 },
      { line: 'meteor_showers', label: 'Метеорные потоки', hours: 3.65 },
    ]);
    expect(hoursByLine(perLine, ['high', 'critical'])).toEqual([]);
    expect(hoursByLine({ meteor_showers: { high: 0.5 }, sep_protons: { high: 1, critical: 0.25 } }, ['high', 'critical'])).toEqual([
      { line: 'sep_protons', label: 'Протоны СЭЧ', hours: 1.25 },
      { line: 'meteor_showers', label: 'Метеорные потоки', hours: 0.5 },
    ]);
  });
});

// Режим «сейчас», 19 сентября 2026: период поиска 16:00–04:00 (720 мин), окно 18:00–00:00 (360 мин). Все интервалы
// протонов none и external_forecast: поток не измерен, уровень взят из прогноза Kp SWPC и прогноза вероятности (D64).
describe('неизмеренный поток протонов: плашка не зелёная и не «данные полные»', () => {
  const START = '2026-09-19T16:00:00Z';
  const END = '2026-09-20T04:00:00Z';
  const WINDOW = { start: '2026-09-19T18:00:00Z', end: '2026-09-20T00:00:00Z', hours: { none: 6, elevated: 0, high: 0, critical: 0, unknown: 0 } };
  const forecastSep = (unmeasured: number | undefined): LineAssessment => {
    const sep = line('sep_protons', [
      interval(START, '2026-09-19T21:06:00Z', 'none', 'external_forecast'),
      interval('2026-09-19T21:06:00Z', '2026-09-20T00:00:00Z', 'none', 'external_forecast'),
      interval('2026-09-20T00:00:00Z', END, 'none', 'external_forecast'),
    ]);
    return unmeasured === undefined ? sep : { ...sep, metrics: { coverage: 1, unmeasured_minutes: unmeasured } };
  };

  it('720 из 720 мин без измерений: жёлтая, причина называет неизмеренные минуты и прогноз', () => {
    const score = lineScore(forecastSep(720), START, END, WINDOW);
    expect(score.percent).toBe(100);
    expect(score.completeness).toBe(1);
    expect(score.unmeasuredMinutes).toBe(720);
    expect(score.windowForecastMinutes).toBe(360);
    expect(hasUnmeasured(score)).toBe(true);
    expect(score.color).toBe('yellow');
    expect(score.reason).not.toContain('данные полные');
    expect(score.reason).toBe('в запрошенном окне только none, но поток не измерен на 720 из 720 мин периода поиска, уровень взят из внешнего прогноза (в запрошенном окне 360 из 360 мин), а не из измерений');
    expect(unmeasuredLabel(score)).toBe('поток не измерен: 720 из 720 мин периода, уровень по прогнозу');
  });

  it('часть периода без измерений тоже не даёт зелёную', () => {
    const score = lineScore(forecastSep(414), START, END, WINDOW);
    expect(score.color).toBe('yellow');
    expect(score.reason).toContain('поток не измерен на 414 из 720 мин периода поиска');
    // Одна неизмеренная минута периода уже не «полные данные».
    expect(lineScore(forecastSep(1), START, END, WINDOW).color).toBe('yellow');
  });

  it('неизмеренный поток не смягчает красную и не красит серую линию', () => {
    const red = lineScore(forecastSep(720), START, END, { ...WINDOW, hours: { none: 5, elevated: 0, high: 1, critical: 0, unknown: 0 } });
    expect(red.color).toBe('red');
    const missing = { ...forecastSep(720), availability: 'missing' as const };
    expect(lineScore(missing, START, END, WINDOW).color).toBe('gray');
  });

  it('измеренный поток (0 мин без измерений) или линия без метрики остаются зелёными при одном none', () => {
    const measured = lineScore(forecastSep(0), START, END, WINDOW);
    expect(measured.color).toBe('green');
    expect(measured.reason).toBe('в запрошенном окне только none, данные полные');
    expect(unmeasuredLabel(measured)).toBeNull();
    const meteors = lineScore({ ...forecastSep(undefined), line: 'meteor_showers' }, START, END, WINDOW);
    expect(meteors.unmeasuredMinutes).toBeNull();
    expect(meteors.windowForecastMinutes).toBeNull();
    expect(meteors.color).toBe('green');
  });

  it('фраза для полноты на карточках расчёта называет линию и неизмеренные минуты', () => {
    const scores = [lineScore(forecastSep(720), START, END, WINDOW)];
    expect(unmeasuredNote(scores)).toBe('Протоны СЭЧ: поток не измерен на 720 из 720 мин периода поиска, уровень взят из внешнего прогноза (в запрошенном окне 360 из 360 мин), а не из измерений. Полнота считает минуты с уровнем, измерен он или взят из прогноза.');
    expect(unmeasuredNote([lineScore(forecastSep(0), START, END, WINDOW)])).toBeNull();
  });
});
