import { describe, expect, it } from 'vitest';
import type { TrajectorySample } from '../api/client';
import { interpolatePoint } from './orbit';

const T0 = Date.UTC(2024, 4, 11, 4, 0);
function sample(minute: number, lat: number, lon: number, alt = 420): TrajectorySample {
  return { t: new Date(T0 + minute * 60000).toISOString(), lat_deg: lat, lon_deg: lon, alt_km: alt, sunlit: true, mlat_deg: null, in_saa: false, level: 'none' };
}

describe('interpolatePoint', () => {
  it('середина между сэмплами это среднее широты, долготы и высоты', () => {
    const p = interpolatePoint([sample(0, 10, 20, 420), sample(2, 14, 28, 422)], T0 + 60000);
    expect(p && [p.lat, p.lon, p.alt]).toEqual([12, 24, 421]);
  });
  it('долгота идёт по короткой дуге через линию перемены дат', () => {
    const p = interpolatePoint([sample(0, 0, 178), sample(2, 0, -178)], T0 + 90000);
    expect(p && p.lon).toBeCloseTo(-179, 9);
  });
  it('вне ряда и на разрыве больше шести минут положения нет', () => {
    const samples = [sample(0, 0, 0), sample(2, 1, 1), sample(30, 5, 5)];
    expect(interpolatePoint(samples, T0 - 1)).toBeNull();
    expect(interpolatePoint(samples, T0 + 31 * 60000)).toBeNull();
    expect(interpolatePoint(samples, T0 + 10 * 60000)).toBeNull();
  });
});
