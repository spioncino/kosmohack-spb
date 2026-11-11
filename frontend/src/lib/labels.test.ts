import { describe, expect, it } from 'vitest';
import { lineBadge } from './labels';

const forecast = { kind: 'external_forecast' as const };
const computed = { kind: 'computed' as const };

describe('lineBadge', () => {
  it('подписывает линию из одних внешних прогнозов как внешний прогноз', () => {
    expect(lineBadge({ availability: 'available', intervals: [forecast, forecast] })).toEqual({ kind: 'external_forecast' });
  });
  it('оставляет плашку доступности при наблюдениях или расчёте', () => {
    expect(lineBadge({ availability: 'available', intervals: [forecast, computed] })).toEqual({ availability: 'available' });
    expect(lineBadge({ availability: 'available', intervals: [] })).toEqual({ availability: 'available' });
  });
  it('не скрывает отсутствие данных за прогнозом', () => {
    expect(lineBadge({ availability: 'missing', intervals: [forecast] })).toEqual({ availability: 'missing' });
  });
});
