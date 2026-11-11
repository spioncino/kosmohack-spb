import { describe, expect, it } from 'vitest';
import { REFRESH_EVERY_MS, REFRESH_MIN_GAP_MS, newestSuccess, refreshPlan, type RefreshInput } from './refresh';

// Расчёт «сейчас» создан в 15:21:10 UTC 19 сентября 2026 (created_at с микросекундами, как в ответе API).
const CREATED = '2026-09-19T15:21:10.045590Z';
const T0 = Date.parse('2026-09-19T15:21:10.045Z');
const MIN = 60000;
const base: RefreshInput = { mode: 'current', computedAt: CREATED, now: T0, visible: true, busy: false, newestData: '2026-09-19T15:20:26Z', lastAttempt: null };
const at = (minutes: number, extra: Partial<RefreshInput> = {}) => refreshPlan({ ...base, now: T0 + minutes * MIN, ...extra });

describe('автообновление расчёта «сейчас»', () => {
  it('интервалы: не чаще раза в 5 мин, без новых данных не реже раза в 10 мин', () => {
    expect(REFRESH_MIN_GAP_MS).toBe(5 * MIN);
    expect(REFRESH_EVERY_MS).toBe(10 * MIN);
  });

  it('без новых данных пересчёт через 10 мин после расчёта, не раньше', () => {
    expect(at(9.9)).toMatchObject({ due: false, reason: 'age', next: T0 + 10 * MIN });
    expect(at(10)).toMatchObject({ due: true, reason: 'age' });
  });

  it('новые данные источника после расчёта: пересчёт через 5 мин после расчёта, не раньше', () => {
    const fresh = { newestData: '2026-09-19T15:26:36Z' };
    expect(at(2, fresh)).toMatchObject({ due: false, reason: 'new_data', next: T0 + 5 * MIN });
    expect(at(5, fresh)).toMatchObject({ due: true, reason: 'new_data' });
  });

  it('разбор и прогноз из прошлого не пересчитываются никогда', () => {
    for (const mode of ['review', 'replay'] as const) {
      expect(at(600, { mode, newestData: '2026-09-19T20:00:00Z' })).toEqual({ due: false, reason: null, next: null });
    }
    expect(at(600, { mode: null })).toEqual({ due: false, reason: null, next: null });
    expect(at(600, { computedAt: null })).toEqual({ due: false, reason: null, next: null });
  });

  it('скрытая вкладка и идущее задание откладывают пересчёт, срок не меняется', () => {
    expect(at(30, { visible: false })).toMatchObject({ due: false, next: T0 + 10 * MIN });
    expect(at(30, { busy: true })).toMatchObject({ due: false, next: T0 + 10 * MIN });
    expect(at(30)).toMatchObject({ due: true });
  });

  it('после неудачной попытки повтор не раньше чем через 5 мин от неё', () => {
    const lastAttempt = T0 + 10 * MIN;
    expect(at(12, { lastAttempt })).toMatchObject({ due: false, next: lastAttempt + 5 * MIN });
    expect(at(15, { lastAttempt })).toMatchObject({ due: true });
  });
});

describe('самое позднее получение данных', () => {
  it('берутся только активные источники с успешным получением', () => {
    expect(newestSuccess([
      { state: 'active', last_success_at: '2026-09-19T15:20:26Z' },
      { state: 'frozen', last_success_at: '2026-09-19T15:59:00Z' },
      { state: 'active', last_success_at: '2026-09-19T15:46:36.235644Z' },
      { state: 'active', last_success_at: null },
    ])).toBe('2026-09-19T15:46:36.235644Z');
    expect(newestSuccess([{ state: 'disabled', last_success_at: '2026-09-19T15:20:26Z' }])).toBeNull();
  });
});
