import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { GLOSSARY, METRIC_TERM, availabilityTerm, docRoute, glossaryEntry, kindTerm, levelTerm, metricTerm } from './glossary';
import { renderMarkdown } from './markdown';

// Документы читаются с диска так же, как их отдаёт GET /manual: docs/<имя> и README.md корня.
const ROOT = new URL('../../../', import.meta.url);
const cache = new Map<string, string[]>();
function idsOf(file: string): string[] {
  if (!cache.has(file)) {
    const path = ['README.md', 'CONTRIBUTING.md'].includes(file) ? file : `docs/${file}`;
    cache.set(file, renderMarkdown(readFileSync(new URL(path, ROOT), 'utf8'), file).ids);
  }
  return cache.get(file)!;
}

// Ключи метрик линий из src/evarisk/risk/lines/*.py.
const BACKEND_METRICS = [
  'access_minutes', 'access_fraction', 'saa_minutes', 'coverage', 'forecast_fraction', 'outlook_fraction', 'persistence_minutes',
  'unmeasured_minutes', 'donki_state_minutes', 'cme_forecast_minutes', 'max_p10_pfu', 'max_p30_pfu', 'max_p100_pfu',
  'shielded_minutes', 'active_showers', 'suit_energy_joule', 'max_factor', 'max_factor_unshielded',
  'shield_threshold_mev', 'dose_proxy_usv_window', 'dose_rate_max_usv_h',
  'sunlit_fraction', 'longest_shadow_minutes', 'starts_in_daylight',
  'kp', 'cutoff_deg', 'max_abs_mlat_deg',
  'conjunctions_in_window', 'coorbiting_rows_in_report', 'min_range_km', 'report_age_hours',
];

describe('glossary', () => {
  it('каждая ссылка ведёт на существующий раздел документа', () => {
    const missing = Object.entries(GLOSSARY)
      .filter(([, e]) => !idsOf(e.doc.file).includes(e.doc.anchor))
      .map(([key, e]) => `${key} → ${e.doc.file}#${e.doc.anchor}`);
    expect(missing).toEqual([]);
  });

  it('покрывает уровни, метки kind, доступность и уверенность', () => {
    for (const level of ['none', 'elevated', 'high', 'critical', 'unknown'] as const) expect(glossaryEntry(levelTerm(level))).not.toBeNull();
    for (const kind of ['observation', 'external_forecast', 'computed'] as const) expect(glossaryEntry(kindTerm(kind))).not.toBeNull();
    for (const a of ['available', 'missing', 'stale', 'unusable_for_replay'] as const) expect(glossaryEntry(availabilityTerm(a))).not.toBeNull();
    expect(glossaryEntry('confidence')).not.toBeNull();
    expect(glossaryEntry('нет такого')).toBeNull();
    expect(glossaryEntry(null)).toBeNull();
  });

  it('у каждой метрики линий есть пояснение', () => {
    expect(BACKEND_METRICS.filter((key) => !glossaryEntry(metricTerm(key)))).toEqual([]);
    expect(Object.values(METRIC_TERM).filter((key) => !GLOSSARY[key])).toEqual([]);
  });

  it('пояснения короткие и с заголовком', () => {
    expect(Object.keys(GLOSSARY).length).toBeGreaterThanOrEqual(35);
    for (const [key, e] of Object.entries(GLOSSARY)) {
      expect(e.title, key).not.toBe('');
      const sentences = e.text.split(/(?<=[.!?»)])\s+(?=[А-ЯЁA-Z«])/).length;
      expect(sentences, key).toBeLessThanOrEqual(3);
    }
  });

  it('ссылка «подробнее» это маршрут раздела', () => {
    expect(docRoute(GLOSSARY.data_gap)).toBe('/manual/DECISIONS.md#d70');
    expect(docRoute(GLOSSARY.penalty)).toBe('/manual/MODEL.md#сравнение-окон');
  });
});
