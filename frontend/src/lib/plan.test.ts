import { describe, expect, it } from 'vitest';
import type { AssessmentRequest, AssessmentResult, Window, WindowScore } from '../api/client';
import { detectPlanChange, isDurationChange, keepPlanChange, planChangeBanner, planSummary, withDuration, type PlanChange } from './plan';

// Прогноз из прошлого на 11 мая 2024 с отсечением 10 мая 12:30, старт 04:00, 6 ч, период поиска сутки.
const base: AssessmentRequest = { mode: 'replay', cutoff: '2024-05-10T12:30:00Z', eva_start: '2024-05-11T04:00:00Z', duration_hours: 6, search_start: '2024-05-11T00:00:00Z', search_end: '2024-05-12T00:00:00Z' };

describe('изменение длительности', () => {
  // Период допустимого начала base: 00:00–18:00; пересчёт на 4 ч сохраняет его и даёт конец поиска 22:00.
  const four = withDuration(base, 4)!;

  it('распознаётся только при том же режиме, отсечении, старте, ограничениях и периоде допустимого начала', () => {
    expect(isDurationChange(base, four)).toBe(true);
    expect(isDurationChange(base, { ...base })).toBe(false);
    expect(isDurationChange(base, { ...four, eva_start: '2024-05-11T05:00:00Z' })).toBe(false);
    expect(isDurationChange(base, { ...four, mode: 'review', cutoff: null })).toBe(false);
    expect(isDurationChange(base, { ...four, cutoff: '2024-05-11T02:00:00Z' })).toBe(false);
    expect(isDurationChange(base, { ...four, search_start: '2024-05-11T02:00:00Z' })).toBe(false);
    expect(isDurationChange(base, { ...four, constraints: { min_sunlit_fraction: 0.8, start_in_daylight: false } })).toBe(false);
    // Тот же период поиска при другой длительности сдвигает конец периода допустимого начала: 00:00–20:00 вместо 00:00–18:00.
    expect(isDurationChange(base, { ...base, duration_hours: 4 })).toBe(false);
  });

  it('ограничения по умолчанию равны отсутствующим, запись времени без секунд равна записи с миллисекундами', () => {
    expect(isDurationChange(base, { ...four, constraints: { min_sunlit_fraction: null, start_in_daylight: false } })).toBe(true);
    expect(isDurationChange(base, { ...four, eva_start: '2024-05-11T04:00:00.000Z', cutoff: '2024-05-10T12:30:00+00:00', search_end: '2024-05-11T22:00:00.000Z' })).toBe(true);
  });

  it('пересчёт сохраняет период допустимого начала и сдвигает конец периода поиска на новую длительность', () => {
    expect(four).toEqual({ ...base, duration_hours: 4, search_end: '2024-05-11T22:00:00Z' });
    expect(withDuration(base, 8)).toMatchObject({ search_start: '2024-05-11T00:00:00Z', search_end: '2024-05-12T02:00:00Z' });
    // Разбор по архиву 10 мая 2024: старт 15:00, период поиска 12:00–22:00 при 6 ч, допустимое начало 12:00–16:00.
    const review: AssessmentRequest = { mode: 'review', cutoff: null, eva_start: '2024-05-10T15:00:00Z', duration_hours: 6, search_start: '2024-05-10T12:00:00Z', search_end: '2024-05-10T22:00:00Z' };
    const longer = withDuration(review, 8);
    expect(longer).toMatchObject({ duration_hours: 8, search_start: '2024-05-10T12:00:00Z', search_end: '2024-05-11T00:00:00Z' });
    expect(isDurationChange(review, longer!)).toBe(true);
    const shorter = withDuration(review, 1.5);
    expect(shorter).toMatchObject({ search_end: '2024-05-10T17:30:00Z' });
    expect(isDurationChange(review, shorter!)).toBe(true);
  });

  it('запрос, не проходящий проверку API, не пересчитывается', () => {
    // Начало ВКД 19:00 позже конца допустимого начала 18:00.
    expect(withDuration({ ...base, eva_start: '2024-05-11T19:00:00Z' }, 4)).toBeNull();
    // Отрезок начал 10 мин короче шага сетки.
    expect(withDuration({ ...base, search_end: '2024-05-11T06:10:00Z' }, 4)).toBeNull();
  });
});

const w = (start: string, end: string): Window => ({ start, end });

function score(window: Window, perLine: WindowScore['per_line']): WindowScore {
  return { window, per_line: perLine, completeness: 1, excluded_reason: null };
}

function result(id: string, request: AssessmentRequest, preferred: Window | null, windows: WindowScore[]): AssessmentResult {
  return {
    id, created_at: '2026-09-19T10:00:00Z', asof: request.cutoff, algorithm_version: 'v', request,
    trajectory: { provider: 'oem', epoch: '2024-05-10T00:00:00Z', is_reconstruction: false, provenance: { raw_id: 'r', source: 'nasa_oem', product: 'oem', published_at: null, published_at_kind: 'unknown', data_version: 'v', url: '' }, age_at_query_hours: null, samples: [] },
    lines: [],
    requested_window: score(w(request.eva_start, new Date(Date.parse(request.eva_start) + request.duration_hours * 3600000).toISOString()), {}),
    windows,
    recommendation: { preferred, reason: 'r', equivalents: [], insufficient: preferred === null, rules_applied: [] },
    sources_used: [], data_availability: {}, limitations: [], rules: [], verification: null,
  };
}

type PerLine = WindowScore['per_line'];
const hours = (high: number, critical: number, elevated = 0): PerLine[string] => ({ none: 0, elevated, high, critical, unknown: 0 });

// Шесть часов: окно 09:30–15:30, протоны high 0.5 ч и critical 0.25 ч, метеоры high 0.2 ч: максимум 45 мин.
// Четыре часа: окно 10:00–14:00, протоны high 0.25 ч и critical 0.1 ч: 21 мин.
const six = result('a6', base, w('2024-05-11T09:30:00Z', '2024-05-11T15:30:00Z'), [
  score(w('2024-05-11T09:00:00Z', '2024-05-11T15:00:00Z'), { sep_protons: hours(1, 0.5) }),
  score(w('2024-05-11T09:30:00Z', '2024-05-11T15:30:00Z'), { sep_protons: hours(0.5, 0.25, 5), meteor_showers: hours(0.2, 0) }),
]);
const four = result('b4', withDuration(base, 4)!, w('2024-05-11T10:00:00Z', '2024-05-11T14:00:00Z'), [
  score(w('2024-05-11T10:00:00Z', '2024-05-11T14:00:00Z'), { sep_protons: hours(0.25, 0.1, 3.65), meteor_showers: hours(0, 0) }),
]);

describe('изменение плана между расчётами', () => {
  it('сводка плана: окно рекомендации и минуты high + critical как максимум по линиям', () => {
    expect(planSummary(six)).toEqual({ id: 'a6', hours: 6, preferred: w('2024-05-11T09:30:00Z', '2024-05-11T15:30:00Z'), highMinutes: 45 });
    expect(planSummary(four).highMinutes).toBe(21);
  });

  it('без окна, без строки окна в сравнении и без линий минут нет', () => {
    expect(planSummary(result('x', base, null, [])).highMinutes).toBeNull();
    expect(planSummary(result('x', base, w('2024-05-11T12:00:00Z', '2024-05-11T18:00:00Z'), [])).highMinutes).toBeNull();
    expect(planSummary(result('x', base, w('2024-05-11T12:00:00Z', '2024-05-11T18:00:00Z'), [score(w('2024-05-11T12:00:00Z', '2024-05-11T18:00:00Z'), {})])).highMinutes).toBeNull();
  });

  it('распознаётся между разными расчётами, отличающимися только длительностью', () => {
    const change = detectPlanChange(six, four);
    expect(change?.from).toMatchObject({ id: 'a6', hours: 6, highMinutes: 45 });
    expect(change?.to).toMatchObject({ id: 'b4', hours: 4, highMinutes: 21 });
    expect(detectPlanChange(null, four)).toBeNull();
    expect(detectPlanChange(six, six)).toBeNull();
    expect(detectPlanChange(six, { ...four, request: { ...four.request, eva_start: '2024-05-11T06:00:00Z' } })).toBeNull();
  });

  it('держится, пока открыт один из двух расчётов, и снимается другим расчётом', () => {
    const change = detectPlanChange(six, four);
    expect(keepPlanChange(change, 'b4')).toBe(change);
    expect(keepPlanChange(change, 'a6')).toBe(change);
    expect(keepPlanChange(change, 'c1')).toBeNull();
    expect(keepPlanChange(null, 'b4')).toBeNull();
  });
});

describe('баннер изменения плана', () => {
  it('сокращение подписано как изменение плана с числами обоих планов', () => {
    const banner = planChangeBanner(detectPlanChange(six, four)!);
    expect(banner.title).toBe('Изменение плана: длительность 6 ч → 4 ч');
    expect(banner.rows).toEqual([
      { label: 'Прежний план, 6 ч', text: 'предпочтительное окно 2024-05-11 09:30–15:30 UTC, high + critical в нём 45 мин (максимум по линиям)' },
      { label: 'Новый план, 4 ч', text: 'предпочтительное окно 2024-05-11 10:00–14:00 UTC, high + critical в нём 21 мин (максимум по линиям)' },
    ]);
    expect(banner.note).toBe('Окна сравниваются только внутри одной длительности: меньше минут после сокращения даёт более короткая работа, а не лучшие условия.');
  });

  it('увеличение получает тот же баннер; дробные часы, отсутствие окна и минут не превращаются в числа', () => {
    const change: PlanChange = {
      from: { id: 'b4', hours: 4.5, preferred: null, highMinutes: null },
      to: { id: 'c6', hours: 6, preferred: w('2024-05-11T09:30:00Z', '2024-05-11T15:30:00Z'), highMinutes: null },
    };
    const banner = planChangeBanner(change);
    expect(banner.title).toBe('Изменение плана: длительность 4,5 ч → 6 ч');
    expect(banner.rows[0].text).toBe('оснований для выбора окна недостаточно');
    expect(banner.rows[1].text).toBe('предпочтительное окно 2024-05-11 09:30–15:30 UTC, high + critical в нём нет данных');
    expect(banner.note).toBe('Окна сравниваются только внутри одной длительности: больше минут после увеличения даёт более длинная работа, а не худшие условия.');
  });
});
