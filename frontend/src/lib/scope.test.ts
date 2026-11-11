import { describe, expect, it } from 'vitest';
import type { AssessmentResult, Level, TrajectorySample } from '../api/client';
import { dangerMinutes, dangerPasses, fraction, hourTicks, levelRuns, periodOf, startsOf } from './scope';

const T0 = Date.UTC(2024, 4, 11, 4, 0);
const iso = (minute: number) => new Date(T0 + minute * 60000).toISOString();

function sample(minute: number, level: Level, mlat: number | null = 10): TrajectorySample {
  return { t: iso(minute), lat_deg: minute, lon_deg: -minute, alt_km: 420, sunlit: true, mlat_deg: mlat, in_saa: false, level };
}

// Сэмплы через 2 минуты: фон, проход high с пиком critical на 04:08, снова фон.
const SAMPLES = [sample(0, 'none'), sample(2, 'none'), sample(4, 'high', 61), sample(6, 'high', 63), sample(8, 'critical', 65), sample(10, 'high', 62), sample(12, 'none'), sample(14, 'none')];

describe('levelRuns', () => {
  it('серия кончается там, где начинается следующая', () => {
    expect(levelRuns(SAMPLES).map((r) => [(r.start - T0) / 60000, (r.end - T0) / 60000, r.level])).toEqual([
      [0, 4, 'none'],
      [4, 8, 'high'],
      [8, 10, 'critical'],
      [10, 12, 'high'],
      [12, 14, 'none'],
    ]);
  });
});

describe('dangerPasses', () => {
  it('соседние high и critical это один проход с наибольшим уровнем и точкой пика магнитной широты', () => {
    const [pass, ...rest] = dangerPasses(SAMPLES);
    expect(rest).toEqual([]);
    expect([(pass.start - T0) / 60000, (pass.end - T0) / 60000, pass.level]).toEqual([4, 12, 'critical']);
    expect([pass.lat, pass.lon, pass.maxAbsMlat]).toEqual([8, -8, 65]);
  });
  it('проход в конце ряда закрывается последним сэмплом', () => {
    const [pass] = dangerPasses([sample(0, 'none'), sample(2, 'high'), sample(4, 'high')]);
    expect([(pass.start - T0) / 60000, (pass.end - T0) / 60000]).toEqual([2, 4]);
  });
  it('без магнитной широты берётся средний сэмпл', () => {
    const [pass] = dangerPasses([sample(0, 'high', null), sample(2, 'high', null), sample(4, 'high', null), sample(6, 'none')]);
    expect([pass.lat, pass.maxAbsMlat]).toEqual([2, null]);
  });
});

describe('dangerMinutes', () => {
  it('считает только high и critical внутри окна', () => {
    const runs = levelRuns(SAMPLES);
    expect(dangerMinutes(runs, { start: T0, end: T0 + 14 * 60000 })).toBe(8);
    expect(dangerMinutes(runs, { start: T0 + 6 * 60000, end: T0 + 9 * 60000 })).toBe(3);
    expect(dangerMinutes(runs, { start: T0 + 12 * 60000, end: T0 + 14 * 60000 })).toBe(0);
  });
});

describe('шкала периода', () => {
  it('доля момента обрезается по краям периода', () => {
    const span = { start: T0, end: T0 + 6 * 3600000 };
    expect([fraction(T0 - 1, span), fraction(T0 + 3 * 3600000, span), fraction(T0 + 7 * 3600000, span)]).toEqual([0, 0.5, 1]);
  });
  it('отметки идут по целым часам: шаг 1 ч до 8 ч, 3 ч для суток', () => {
    const short = hourTicks({ start: T0 + 30 * 60000, end: T0 + 3 * 3600000 });
    expect(short.map((t) => new Date(t).getUTCHours())).toEqual([5, 6, 7]);
    const day = hourTicks({ start: Date.UTC(2024, 4, 11, 0, 0), end: Date.UTC(2024, 4, 12, 0, 0) });
    expect(day.map((t) => new Date(t).getUTCHours())).toEqual([0, 3, 6, 9, 12, 15, 18, 21, 0]);
  });
});

describe('periodOf и startsOf', () => {
  it('период поиска 00:00–24:00 при 6 ч ВКД: окна до 24:00, допустимое начало до 18:00', () => {
    const result = { request: { mode: 'replay', cutoff: '2024-05-10T12:30:00Z', eva_start: '2024-05-11T04:00:00Z', duration_hours: 6, search_start: '2024-05-11T00:00:00Z', search_end: '2024-05-12T00:00:00Z' } } as AssessmentResult;
    expect(periodOf(result)).toEqual({ start: Date.UTC(2024, 4, 11, 0), end: Date.UTC(2024, 4, 12, 0) });
    expect(startsOf(result)).toEqual({ start: Date.UTC(2024, 4, 11, 0), end: Date.UTC(2024, 4, 11, 18) });
  });
});
