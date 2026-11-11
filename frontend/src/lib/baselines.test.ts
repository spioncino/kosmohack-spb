import { describe, expect, it } from 'vitest';
import type { AssessmentResult, BaselineCheck, Interval, Provenance, Verification } from '../api/client';
import { comparisonRows, exposureCounts, highestLevel, requestedExposureText } from './baselines';

function prov(rawId: string, source: string, product: string, publishedAt: string): Provenance {
  return { raw_id: rawId, source, product, published_at: publishedAt, published_at_kind: 'exact', data_version: 'v1', url: 'https://example' };
}

const forecast = prov('r-3day', 'swpc_archive', '3day_forecast', '2024-05-10T12:30:00Z');
const kp = prov('r-kp', 'swpc_archive', 'geomag_forecast', '2024-05-10T12:30:00Z');
const goes = prov('r-goes', 'goes_sgps', 'avg5m', '2024-05-10T00:05:00Z');

function interval(start: string, end: string, level: Interval['level'], kind: Interval['kind'], provenance: Provenance[]): Interval {
  return { start, end, level, kind, provenance, rule: 'правило', note: '', start_uncertainty: null, end_uncertainty: null };
}

// Прогноз из прошлого на 11 мая 2024 с отсечением 10 мая 12:30: запрошено 04:00–10:00, период поиска сутки.
// Наш выбор 09:30–15:30, экспозиции по факту в нём 0 мин; попаданий 93, пропусков 14, ложных 18 мин
// экспозиции, заблаговременность 828 мин (первый факт ≥ high в 02:18). В запрошенном окне по факту 74 мин
// экспозиции (src/evarisk/risk/README.md: 74 минуты доступа critical в окне 04:00–10:00).
const predicted = [
  interval('2024-05-11T00:00:00Z', '2024-05-11T02:18:00Z', 'elevated', 'external_forecast', [forecast]),
  interval('2024-05-11T02:18:00Z', '2024-05-11T02:25:00Z', 'high', 'computed', [forecast, kp]),
  interval('2024-05-11T02:25:00Z', '2024-05-12T00:00:00Z', 'elevated', 'external_forecast', [forecast]),
];

// Персистентность: последний отсчёт p10 ниже S1 на весь период, окна не различаются, запрошенное окно остаётся.
const persistence: BaselineCheck = {
  name: 'persistence',
  title: 'персистентность',
  rule: 'уровень по шкале S последнего отсчёта p10 на весь период поиска',
  level: 'none',
  basis: 'отсчёт GOES p10 2024-05-09 23:55 UTC, давность 12 ч 35 мин: ниже S1',
  provenance: [goes],
  preferred: { start: '2024-05-11T04:00:00Z', end: '2024-05-11T10:00:00Z' },
  insufficient: false,
  preferred_exposure_minutes: 74,
  exposure_hit_minutes: 0,
  exposure_miss_minutes: 107,
  exposure_false_alarm_minutes: 0,
  lead_time_minutes: null,
  window_choice_stable: false,
  note: 'доступ частиц по траектории не учитывается',
};

// Прогноз SWPC без орбиты при отсутствии выпуска до отсечения: уровня нет, окна нет, сверять нечего.
const outlookMissing: BaselineCheck = {
  name: 'swpc_outlook',
  title: 'прогноз SWPC без орбиты',
  rule: 'вероятность S1 из 3-day forecast на весь период поиска',
  level: 'unknown',
  basis: 'выпуска до отсечения нет',
  provenance: [],
  preferred: null,
  insufficient: true,
  preferred_exposure_minutes: null,
  exposure_hit_minutes: null,
  exposure_miss_minutes: null,
  exposure_false_alarm_minutes: null,
  lead_time_minutes: null,
  window_choice_stable: false,
  note: '',
};

const verification: Verification = {
  truth_asof: null,
  line: 'sep_protons',
  predicted,
  observed: [],
  observed_availability: 'available',
  observed_preferred: { start: '2024-05-11T12:00:00Z', end: '2024-05-11T18:00:00Z' },
  observed_insufficient: false,
  lead_time_minutes: 828,
  first_observed_alarm: '2024-05-11T02:18:00Z',
  first_predicted_warning: '2024-05-11T00:00:00Z',
  hit_minutes: 0,
  miss_minutes: 0,
  false_alarm_minutes: 0,
  exposure_hit_minutes: 93,
  exposure_miss_minutes: 14,
  exposure_false_alarm_minutes: 18,
  quiet_minutes: 0,
  unverified_minutes: 0,
  predicted_unknown_minutes: 0,
  window_choice_stable: true,
  note: '',
  requested_exposure_minutes: 74,
  preferred_exposure_minutes: 0,
  baselines: [persistence, outlookMissing],
};

function result(v: Verification | null): AssessmentResult {
  return {
    id: 'replay-0511', created_at: '2026-09-19T10:00:00Z', asof: '2024-05-10T12:30:00Z', algorithm_version: 'v',
    request: { mode: 'replay', cutoff: '2024-05-10T12:30:00Z', eva_start: '2024-05-11T04:00:00Z', duration_hours: 6, search_start: '2024-05-11T00:00:00Z', search_end: '2024-05-12T00:00:00Z' },
    trajectory: { provider: 'oem', epoch: '2024-05-10T00:00:00Z', is_reconstruction: false, provenance: kp, age_at_query_hours: null, samples: [] },
    lines: [],
    requested_window: { window: { start: '2024-05-11T04:00:00Z', end: '2024-05-11T10:00:00Z' }, per_line: {}, completeness: 1, excluded_reason: null },
    windows: [],
    recommendation: { preferred: { start: '2024-05-11T09:30:00Z', end: '2024-05-11T15:30:00Z' }, reason: 'r', equivalents: [], insufficient: false, rules_applied: [] },
    sources_used: [], data_availability: {}, limitations: [], rules: [], verification: v,
  };
}

describe('сравнение с простыми подходами', () => {
  it('наша строка берёт окно рекомендации и поля сверки', () => {
    const [ours] = comparisonRows(result(verification));
    expect(ours).toMatchObject({
      key: 'ours',
      ours: true,
      level: 'high',
      window: '2024-05-11 09:30–15:30 UTC',
      exposureMinutes: 0,
      exposure: '0',
      counts: '93 / 14 / 18',
      lead: '828 мин',
      stable: true,
    });
    expect(ours.basis).toBe('интервалы линии «Протоны СЭЧ»: 3 (внешний прогноз, наш расчёт), данные до отсечения 2024-05-10 12:30 UTC');
    expect(ours.provenance.map((p) => p.raw_id)).toEqual(['r-3day', 'r-kp']);
  });

  it('персистентность сохраняет запрошенное окно и получает в нём 74 мин экспозиции', () => {
    const rows = comparisonRows(result(verification));
    expect(rows.map((row) => row.key)).toEqual(['ours', 'persistence', 'swpc_outlook']);
    expect(rows[1]).toMatchObject({
      title: 'персистентность',
      ours: false,
      level: 'none',
      basis: 'отсчёт GOES p10 2024-05-09 23:55 UTC, давность 12 ч 35 мин: ниже S1',
      window: '2024-05-11 04:00–10:00 UTC',
      exposureMinutes: 74,
      exposure: '74',
      counts: '0 / 107 / 0',
      lead: 'не определена',
      stable: false,
    });
    expect(rows[1].provenance).toEqual([goes]);
  });

  it('подход без окна и без чисел не получает благоприятных значений', () => {
    const row = comparisonRows(result(verification))[2];
    expect(row).toMatchObject({ level: 'unknown', window: 'оснований для выбора нет', exposureMinutes: null, exposure: 'окна нет', counts: 'нет данных', lead: 'не определена' });
  });

  it('строка запрошенного окна: число, null как «нет данных», у ранних расчётов строки нет', () => {
    expect(requestedExposureText(result(verification))).toBe('В запрошенном окне 2024-05-11 04:00–10:00 UTC по факту: 74 мин экспозиции');
    expect(requestedExposureText(result({ ...verification, requested_exposure_minutes: null }))).toBe('В запрошенном окне 2024-05-11 04:00–10:00 UTC по факту: нет данных');
    const { requested_exposure_minutes: _r, preferred_exposure_minutes: _p, baselines: _b, ...older } = verification;
    expect(requestedExposureText(result(older))).toBeNull();
    expect(requestedExposureText(result(null))).toBeNull();
  });

  it('без поля baselines, с пустым списком и без сверки таблицы нет; без числа в нашей строке «нет данных»', () => {
    const { baselines: _b, preferred_exposure_minutes: _p, ...older } = verification;
    expect(comparisonRows(result(older))).toEqual([]);
    expect(comparisonRows(result({ ...verification, baselines: [] }))).toEqual([]);
    expect(comparisonRows(result(null))).toEqual([]);
    const ours = comparisonRows(result({ ...verification, preferred_exposure_minutes: null, exposure_hit_minutes: null, exposure_miss_minutes: null, exposure_false_alarm_minutes: null }))[0];
    expect(ours).toMatchObject({ exposure: 'нет данных', exposureMinutes: null, counts: 'нет данных' });
  });

  it('наш расчёт без рекомендации не показывает окно и экспозицию', () => {
    const insufficient = { ...result(verification), recommendation: { preferred: null, reason: 'r', equivalents: [], insufficient: true, rules_applied: [] } };
    expect(comparisonRows(insufficient)[0]).toMatchObject({ window: 'оснований для выбора нет', exposure: 'окна нет', exposureMinutes: null });
  });
});

describe('наибольший уровень и счёт по минутам экспозиции', () => {
  it('unknown только без известных уровней, без интервалов null', () => {
    expect(highestLevel([])).toBeNull();
    expect(highestLevel([interval('2024-05-11T00:00:00Z', '2024-05-11T01:00:00Z', 'unknown', 'computed', [])])).toBe('unknown');
    expect(highestLevel(predicted)).toBe('high');
  });

  it('отсутствующее число пишется «нет данных», а не нулём', () => {
    expect(exposureCounts(93, null, 18)).toBe('93 / нет данных / 18');
    expect(exposureCounts(undefined, undefined, undefined)).toBe('нет данных');
  });
});
