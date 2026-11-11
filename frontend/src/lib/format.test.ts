import { describe, expect, it } from 'vitest';
import { ago, cadence, fmtTime, fmtUtc, fmtWindow, fromLocalInput, isoZ, toLocalInput } from './format';

describe('время в UTC', () => {
  it('форматирует ISO с пометкой UTC независимо от локальной зоны', () => {
    expect(fmtUtc('2024-05-11T04:00:00Z')).toBe('2024-05-11 04:00 UTC');
    expect(fmtUtc('2024-05-10T12:30:00+00:00', true)).toBe('2024-05-10 12:30:00 UTC');
    expect(fmtUtc(null)).toBe('—');
    expect(fmtTime('2024-05-11T17:18:00Z')).toBe('17:18');
  });

  it('окно в пределах суток пишется с одной датой, через полночь с двумя', () => {
    expect(fmtWindow({ start: '2024-05-11T09:30:00Z', end: '2024-05-11T15:30:00Z' })).toBe('2024-05-11 09:30–15:30 UTC');
    expect(fmtWindow({ start: '2024-05-10T22:00:00Z', end: '2024-05-11T04:00:00Z' })).toBe('2024-05-10 22:00 – 2024-05-11 04:00 UTC');
  });

  it('isoZ убирает миллисекунды и сохраняет Z', () => {
    expect(isoZ(Date.UTC(2024, 4, 11, 4, 0, 0, 123))).toBe('2024-05-11T04:00:00Z');
  });

  it('EPOCH из OMM без зоны читается как UTC', () => {
    expect(fmtUtc('2026-09-18T03:25:38.782272', true)).toBe('2026-09-18 03:25:38 UTC');
    expect(isoZ('2026-09-18T03:25:38.782272')).toBe('2026-09-18T03:25:38Z');
  });

  it('datetime-local трактуется как UTC в обе стороны', () => {
    expect(toLocalInput('2024-05-11T04:00:00Z')).toBe('2024-05-11T04:00');
    expect(fromLocalInput('2024-05-11T04:00')).toBe('2024-05-11T04:00:00Z');
    expect(fromLocalInput('')).toBeNull();
  });

  it('давность и период опроса читаются по-русски', () => {
    const now = Date.UTC(2026, 8, 18, 21, 30);
    expect(ago('2026-09-18T21:29:30Z', now)).toBe('только что');
    expect(ago('2026-09-18T21:00:00Z', now)).toBe('30 мин назад');
    expect(ago('2026-09-18T15:36:07Z', now)).toBe('5 ч назад');
    expect(ago('2026-09-10T00:00:00Z', now)).toBe('8 дн назад');
    expect(ago(null, now)).toBe('никогда');
    expect(cadence(300)).toBe('5 мин');
    expect(cadence(21600)).toBe('6 ч');
    expect(cadence(2592000)).toBe('30 дн');
  });
});
