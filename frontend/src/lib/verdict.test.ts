import { describe, expect, it } from 'vitest';
import type { AssessmentResult, Interval, LineAssessment, Verification, Window } from '../api/client';
import { equivalentsExplanation, verificationVerdict, warnedInAdvance } from './verdict';

const provenance = [{ raw_id: 'r1', source: 'swpc_archive', product: '3day_forecast', published_at: '2024-05-10T12:30:00Z', published_at_kind: 'exact' as const, data_version: 'v1', url: 'https://example' }];

function interval(start: string, end: string, level: Interval['level'], kind: Interval['kind'] = 'computed'): Interval {
  return { start, end, level, kind, provenance, rule: 'правило', note: '', start_uncertainty: null, end_uncertainty: null };
}

// Сверка replay 11 мая 2024 с отсечением 10 мая 12:30 (docs/experiments): прогноз elevated на весь период
// по вероятности SWPC 55 %, факт содержит critical с 03:05, попаданий 0, пропущено 32 мин, ложных тревог 0,
// заблаговременность 875 мин, окно по прогнозу 00:00–06:00, по факту 08:00–14:00.
const replay: Verification = {
  truth_asof: null,
  line: 'sep_protons',
  predicted: [interval('2024-05-11T00:00:00Z', '2024-05-11T14:00:00Z', 'elevated', 'external_forecast')],
  observed: [interval('2024-05-11T00:00:00Z', '2024-05-11T03:05:00Z', 'elevated'), interval('2024-05-11T03:05:00Z', '2024-05-11T03:13:00Z', 'critical')],
  observed_availability: 'available',
  observed_preferred: { start: '2024-05-11T08:00:00Z', end: '2024-05-11T14:00:00Z' },
  observed_insufficient: false,
  lead_time_minutes: 875,
  first_observed_alarm: '2024-05-11T03:05:00Z',
  first_predicted_warning: '2024-05-11T00:00:00Z',
  hit_minutes: 0,
  miss_minutes: 32,
  false_alarm_minutes: 0,
  quiet_minutes: 808,
  unverified_minutes: 0,
  predicted_unknown_minutes: 0,
  window_choice_stable: false,
  note: 'текст note в вердикте не используется',
};

describe('вердикт сверки', () => {
  it('предупреждение заранее, high не достигнут без измерений потока, выбор окна не подтвердился', () => {
    expect(warnedInAdvance(replay)).toBe(true);
    expect(verificationVerdict(replay)).toBe(
      'Предупреждение выдано заранее: за 875 мин, ложных тревог нет; уровень high не достигнут: измерений потока на момент отсечения не было, пропущено 32 мин; после события лучшее окно оказалось иным',
    );
  });

  it('предупреждение заранее с попаданиями и совпавшим выбором окна', () => {
    const hit: Verification = {
      ...replay,
      predicted: [interval('2024-05-11T00:00:00Z', '2024-05-11T02:00:00Z', 'elevated', 'external_forecast'), interval('2024-05-11T02:00:00Z', '2024-05-11T14:00:00Z', 'high', 'observation')],
      lead_time_minutes: 430,
      hit_minutes: 200,
      miss_minutes: 12,
      observed_preferred: { start: '2024-05-11T08:00:00Z', end: '2024-05-11T14:00:00Z' },
      window_choice_stable: true,
    };
    expect(verificationVerdict(hit)).toBe('Предупреждение выдано заранее: за 430 мин, ложных тревог нет; попаданий 200 мин; выбор окна совпал с окном по факту');
  });

  it('предупреждение после факта с ложными тревогами и пропусками при измерениях потока', () => {
    const late: Verification = {
      ...replay,
      predicted: [interval('2024-05-11T00:00:00Z', '2024-05-11T05:00:00Z', 'none', 'observation'), interval('2024-05-11T05:00:00Z', '2024-05-11T14:00:00Z', 'high', 'observation')],
      first_predicted_warning: '2024-05-11T05:00:00Z',
      lead_time_minutes: null,
      hit_minutes: 0,
      miss_minutes: 32,
      false_alarm_minutes: 40,
      window_choice_stable: true,
      observed_preferred: null,
    };
    expect(warnedInAdvance(late)).toBe(false);
    expect(verificationVerdict(late)).toBe('Предупреждение выдано после первого факта ≥ high; уровень high не достигнут, пропущено 32 мин');
  });

  it('без факта ≥ high и без данных факта вердикт не выдумывает предупреждение', () => {
    const quiet: Verification = { ...replay, first_observed_alarm: null, lead_time_minutes: null, miss_minutes: 0, quiet_minutes: 840, window_choice_stable: true, observed_preferred: { start: '2024-05-11T00:00:00Z', end: '2024-05-11T06:00:00Z' } };
    expect(verificationVerdict(quiet)).toBe('Факт ≥ high в периоде не наступил; выбор окна совпал с окном по факту');
    const missing: Verification = { ...quiet, observed_availability: 'missing', observed_preferred: null, unverified_minutes: 840, quiet_minutes: 0 };
    expect(verificationVerdict(missing)).toBe('Факта для сверки нет');
    const noWarning: Verification = { ...replay, first_predicted_warning: null, lead_time_minutes: null, predicted: [interval('2024-05-11T00:00:00Z', '2024-05-11T14:00:00Z', 'none', 'observation')] };
    expect(verificationVerdict(noWarning)).toBe('Предупреждения не было: прогноз не дал уровня ≥ elevated; уровень high не достигнут, пропущено 32 мин; после события лучшее окно оказалось иным');
  });
});

function line(name: string, intervals: Interval[], availability: LineAssessment['availability'] = 'available'): LineAssessment {
  return { line: name, mechanism: 'm', intervals, availability, confidence: { grade: 'low', depends_on: 'x' }, limitations: [], chain_ids: [], metrics: {} };
}

const window = (start: string): Window => ({ start, end: `2024-05-11T${String(Number(start.slice(11, 13)) + 6).padStart(2, '0')}:00:00Z` });

function result(lines: LineAssessment[], preferred: Window | null, equivalents: Window[], windowCount: number): AssessmentResult {
  return {
    id: 'a', created_at: '2024-05-10T12:30:00Z', asof: '2024-05-10T12:30:00Z', algorithm_version: 'v',
    request: { mode: 'replay', cutoff: '2024-05-10T12:30:00Z', eva_start: '2024-05-11T04:00:00Z', duration_hours: 6, search_start: '2024-05-11T00:00:00Z', search_end: '2024-05-11T14:00:00Z' },
    trajectory: { provider: 'oem', epoch: '2024-05-10T00:00:00Z', is_reconstruction: false, provenance: provenance[0], age_at_query_hours: null, samples: [] },
    lines,
    requested_window: { window: window('2024-05-11T04:00:00Z'), per_line: {}, completeness: 1, excluded_reason: null },
    windows: Array.from({ length: windowCount }, (_, i) => ({ window: window(`2024-05-11T${String(i).padStart(2, '0')}:00:00Z`), per_line: {}, completeness: 1, excluded_reason: null })),
    recommendation: { preferred, reason: 'текст reason в объяснении не используется', equivalents, insufficient: preferred === null, rules_applied: [] },
    sources_used: [], data_availability: {}, limitations: [], rules: [], verification: null,
  };
}

describe('объяснение равнозначных окон', () => {
  const forecastOnly = line('sep_protons', [interval('2024-05-11T00:00:00Z', '2024-05-11T03:06:00Z', 'elevated', 'external_forecast'), interval('2024-05-11T03:06:00Z', '2024-05-11T14:00:00Z', 'elevated', 'external_forecast')]);
  const measured = line('sep_protons', [interval('2024-05-11T00:00:00Z', '2024-05-11T03:06:00Z', 'elevated', 'external_forecast'), interval('2024-05-11T03:06:00Z', '2024-05-11T14:00:00Z', 'critical', 'observation')]);
  const equivalents = Array.from({ length: 7 }, (_, i) => window(`2024-05-11T${String(i + 1).padStart(2, '0')}:00:00Z`));

  it('линия протонов целиком одного уровня из внешнего прогноза объясняет равнозначность', () => {
    expect(equivalentsExplanation(result([forecastOnly], window('2024-05-11T00:00:00Z'), equivalents, 8))).toBe(
      'Окна не различаются: уровень на весь период взят из внешнего прогноза без измерений потока; выбрано самое раннее',
    );
  });

  it('без «выбрано самое раннее», если предпочтительное окно не самое раннее среди равнозначных', () => {
    expect(equivalentsExplanation(result([forecastOnly], window('2024-05-11T03:00:00Z'), equivalents, 8))).toBe('Окна не различаются: уровень на весь период взят из внешнего прогноза без измерений потока');
  });

  it('нет объяснения при измерениях потока, при малой доле равнозначных, без равнозначных и без рекомендации', () => {
    expect(equivalentsExplanation(result([measured], window('2024-05-11T00:00:00Z'), equivalents, 8))).toBeNull();
    expect(equivalentsExplanation(result([forecastOnly], window('2024-05-11T00:00:00Z'), equivalents.slice(0, 2), 8))).toBeNull();
    expect(equivalentsExplanation(result([forecastOnly], window('2024-05-11T00:00:00Z'), [], 8))).toBeNull();
    expect(equivalentsExplanation(result([forecastOnly], null, equivalents, 8))).toBeNull();
    expect(equivalentsExplanation(result([line('sep_protons', [], 'missing')], window('2024-05-11T00:00:00Z'), equivalents, 8))).toBeNull();
  });
});
