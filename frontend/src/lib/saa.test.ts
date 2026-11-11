import { describe, expect, it } from 'vitest';
import type { SaaPass } from '../api/client';
import { saaPassInterval } from './saa';

const pass: SaaPass = {
  start: '2024-05-11T07:23:00Z',
  end: '2024-05-11T07:41:00Z',
  min_b_nt: 21414,
  min_at: '2024-05-11T07:32:00Z',
  threshold_nt: 24000,
  rule: 'ЮАА по трассе: модуль поля IGRF ниже 24000 нТл; Heirtzler, NTRS 20000085550',
  note: 'минимум |B| 21414 нТл в 2024-05-11 07:32 UTC',
  provenance: [{ raw_id: 'oem1', source: 'nasa_oem', product: 'oem', published_at: '2024-05-10T18:51:00Z', published_at_kind: 'exact', data_version: 'oem', url: 'https://example' }],
};

describe('saaPassInterval', () => {
  it('переносит границы, правило, значение и провенанс прохода в интервал картины', () => {
    const iv = saaPassInterval(pass);
    expect(iv.start).toBe(pass.start);
    expect(iv.end).toBe(pass.end);
    expect(iv.rule).toContain('NTRS 20000085550');
    expect(iv.note).toContain('21414');
    expect(iv.provenance[0].raw_id).toBe('oem1');
    expect(iv.kind).toBe('computed');
  });
});
