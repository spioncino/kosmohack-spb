import type { Interval, SaaPass } from '../api/client';

// Имя строки картины для проходов через ЮАА: подпись в LINE_LABEL, панель деталей не показывает уровень.
export const SAA_LINE = 'saa';

// Проход через ЮАА в форме интервала картины: та же панель деталей с правилом, значением и провенансом,
// что у интервалов линий; уровень риска у прохода отсутствует, поэтому level это «фон», а kind «наш расчёт».
export function saaPassInterval(pass: SaaPass): Interval {
  return {
    start: pass.start,
    end: pass.end,
    level: 'none',
    kind: 'computed',
    provenance: pass.provenance,
    rule: pass.rule,
    note: pass.note,
    start_uncertainty: null,
    end_uncertainty: null,
  };
}
