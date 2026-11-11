import { describe, expect, it } from 'vitest';
import type { ConjunctionRow, ConjunctionsLatest } from '../api/client';
import {
  HISTORICAL_TEXT,
  LEVEL_HINT,
  NO_REPORT_TEXT,
  emptyText,
  fmtDay,
  fmtMaxprob,
  intervalText,
  isHistorical,
  isPast,
  isStale,
  reportState,
  reportTimeText,
  sortByTca,
} from './conjunctions';

// Отчёт SOCRATES по МКС из tests/fixtures/socrates/table_iss_2026-09-17.html: «Data current as of 2026 Sep 17
// 22:14:23 UTC», интервал расчёта 17 сен 22:00 – 24 сен 22:00, три сближения в порядке минимальной дистанции.
const smdc: ConjunctionRow = { tca: '2026-09-21T21:20:21.992000Z', secondary_name: 'SMDC ONE 2.4', secondary_catnr: 39469, min_range_km: 1.732, rel_speed_kms: 15.319, max_probability: 3.045e-5, level: 'elevated', coorbiting: false };
const flock: ConjunctionRow = { tca: '2026-09-24T04:59:21.752000Z', secondary_name: 'FLOCK 4BE-31', secondary_catnr: 60488, min_range_km: 3.507, rel_speed_kms: 14.751, max_probability: 9.119e-6, level: 'none', coorbiting: false };
const objectN: ConjunctionRow = { tca: '2026-09-23T07:34:27.941000Z', secondary_name: 'OBJECT N', secondary_catnr: 64888, min_range_km: 4.979, rel_speed_kms: 9.422, max_probability: 8.8e-6, level: 'none', coorbiting: false };

const report: ConjunctionsLatest = {
  available: true,
  data_as_of: '2026-09-17T22:14:23Z',
  age_hours: 15.26,
  stale: false,
  max_report_age_hours: 24,
  interval_start: '2026-09-17T22:00:00Z',
  interval_stop: '2026-09-24T22:00:00Z',
  provenance: { raw_id: 'r-socrates', source: 'socrates', product: 'conjunctions_iss', published_at: '2026-09-17T22:14:23Z', published_at_kind: 'exact', data_version: 'run 2026-09-17T22:14:23+00:00', url: 'https://celestrak.org/SOCRATES/table-socrates.php?NAME=ISS%20(ZARYA)&ORDER=MINRANGE&MAX=100' },
  conjunctions: [smdc, flock, objectN],
  coorbiting_rows: 0,
  limitations: [],
};

const missing: ConjunctionsLatest = { ...report, available: false, data_as_of: null, age_hours: null, interval_start: null, interval_stop: null, provenance: null, conjunctions: [], limitations: ['отчёта SOCRATES в хранилище нет'] };

// 18 сентября 13:30 UTC: отчёту 15 ч 15 мин.
const now = Date.parse('2026-09-18T13:30:00Z');

describe('состояние карточки текущего отчёта', () => {
  it('отчёт со сближениями, пустой отчёт и отсутствие отчёта различаются', () => {
    expect(reportState(report)).toBe('list');
    expect(reportState({ ...report, conjunctions: [], coorbiting_rows: 55 })).toBe('empty');
    expect(reportState(missing)).toBe('missing');
  });

  it('без отчёта подпись «отчёта SOCRATES нет», а не «сближений нет»', () => {
    expect(reportTimeText(missing, now)).toBe(NO_REPORT_TEXT);
    expect(reportTimeText(missing, now)).not.toContain('нет сближений');
    expect(intervalText(missing, now)).toBeNull();
    expect(isStale(missing, now)).toBe(false);
  });

  it('пустой отчёт говорит о со-орбитальных объектах, если они были', () => {
    expect(emptyText({ ...report, conjunctions: [], coorbiting_rows: 55 })).toBe('в отчёте нет сближений, кроме со-орбитальных объектов');
    expect(emptyText({ ...report, conjunctions: [], coorbiting_rows: 0 })).toBe('в отчёте нет сближений');
  });
});

describe('строки отчёта', () => {
  it('сортируются по TCA, исходный порядок по дистанции не меняется', () => {
    expect(sortByTca(report.conjunctions).map((c) => c.secondary_name)).toEqual(['SMDC ONE 2.4', 'OBJECT N', 'FLOCK 4BE-31']);
    expect(report.conjunctions.map((c) => c.secondary_name)).toEqual(['SMDC ONE 2.4', 'FLOCK 4BE-31', 'OBJECT N']);
  });

  it('MAXPROB в экспоненциальной записи как в отчёте', () => {
    expect(fmtMaxprob(smdc.max_probability)).toBe('3.045e-05');
    expect(fmtMaxprob(flock.max_probability)).toBe('9.119e-06');
    expect(fmtMaxprob(objectN.max_probability)).toBe('8.800e-06');
    expect(fmtMaxprob(1.66e-2)).toBe('1.660e-02');
    expect(fmtMaxprob(null)).toBe('—');
    expect(fmtMaxprob(Number.NaN)).toBe('—');
  });

  it('подсказка уровня называет порог MAXPROB', () => {
    expect(LEVEL_HINT[smdc.level]).toContain('не ниже 1e-5');
    expect(LEVEL_HINT[objectN.level]).toContain('ниже 1e-5');
    expect(LEVEL_HINT.high).toContain('1e-4');
  });

  it('прошедшее сближение отмечается относительно now', () => {
    expect(isPast(smdc.tca, now)).toBe(false);
    expect(isPast(smdc.tca, Date.parse('2026-09-22T00:00:00Z'))).toBe(true);
  });
});

describe('время, давность и интервал отчёта', () => {
  it('«отчёт на 17 сен 22:14 UTC, 15 ч назад»', () => {
    expect(reportTimeText(report, now)).toBe('отчёт на 17 сен 22:14 UTC, 15 ч назад');
    expect(reportTimeText(report, Date.parse('2026-09-17T22:40:00Z'))).toBe('отчёт на 17 сен 22:14 UTC, 25 мин назад');
  });

  it('интервал расчёта семь суток', () => {
    expect(intervalText(report, now)).toBe('интервал расчёта 17 сен 22:00 – 24 сен 22:00 UTC (7 сут)');
  });

  it('год указывается, только если отличается от текущего', () => {
    expect(fmtDay('2026-05-11T04:00:00Z', now)).toBe('11 мая 04:00');
    expect(fmtDay('2025-12-31T23:59:00Z', now)).toBe('31 дек 2025 23:59');
  });

  it('устаревание по признаку API и по давности на открытой странице', () => {
    expect(isStale(report, now)).toBe(false);
    expect(isStale({ ...report, stale: true }, now)).toBe(true);
    // 19 сентября 00:00: отчёту 25 ч 46 мин, больше 24 ч, хотя API при загрузке ответил stale = false.
    expect(isStale(report, Date.parse('2026-09-19T00:00:00Z'))).toBe(true);
  });
});

describe('подписи', () => {
  it('исторические режимы объясняют отсутствие сближений архивом SOCRATES', () => {
    expect(isHistorical('review')).toBe(true);
    expect(isHistorical('replay')).toBe(true);
    expect(isHistorical('current')).toBe(false);
    expect(HISTORICAL_TEXT).toBe('Для исторических дат сближений нет ни у кого: CelesTrak SOCRATES хранит только текущий отчёт, архива нет (D29). Текущие сближения на ближайшие 7 суток показаны выше.');
  });
});
