import type { AssessmentResult, Verification } from '../api/client';
import { ms } from './format';

const EQUIVALENTS_TEXT = 'Окна не различаются: уровень на весь период взят из внешнего прогноза без измерений потока';

// Объяснение, почему большинство окон равнозначно: у линии протонов все интервалы одного уровня и
// все из внешнего прогноза, различать окна нечем. Условие берётся из интервалов результата, не из
// текста reason. Показывается, когда равнозначных окон не меньше половины кандидатов.
export function equivalentsExplanation(result: AssessmentResult): string | null {
  const rec = result.recommendation;
  if (rec.insufficient || !rec.preferred || !rec.equivalents.length) return null;
  if (rec.equivalents.length * 2 < result.windows.length) return null;
  const sep = result.lines.find((line) => line.line === 'sep_protons');
  if (!sep || sep.availability !== 'available' || !sep.intervals.length) return null;
  const level = sep.intervals[0].level;
  if (!sep.intervals.every((iv) => iv.level === level && iv.kind === 'external_forecast')) return null;
  const preferredStart = ms(rec.preferred.start);
  const earliest = rec.equivalents.every((w) => ms(w.start) >= preferredStart);
  return earliest ? `${EQUIVALENTS_TEXT}; выбрано самое раннее` : EQUIVALENTS_TEXT;
}

const minutes = (value: number) => `${Math.round(value)} мин`;

// Предупреждение считается выданным заранее, если прогноз дал уровень ≥ elevated раньше первого факта ≥ high.
export function warnedInAdvance(v: Verification): boolean {
  return v.first_predicted_warning !== null && v.first_observed_alarm !== null && ms(v.first_predicted_warning) < ms(v.first_observed_alarm);
}

// Экспозиция экипажа отдельно от уровня обстановки: минуты, когда протоны доставали станцию на витках доступа.
// Пропуски уровня без пропусков экспозиции означают, что станция была закрыта магнитным полем.
export function exposureVerdict(v: Verification): string | null {
  if (v.exposure_hit_minutes == null || v.exposure_miss_minutes == null || v.exposure_false_alarm_minutes == null) return null;
  const hit = v.exposure_hit_minutes;
  const miss = v.exposure_miss_minutes;
  const falseAlarm = v.exposure_false_alarm_minutes;
  if (hit === 0 && miss === 0) {
    return `экспозиции экипажа по факту не было${falseAlarm > 0 ? `, ложных тревог ${minutes(falseAlarm)}` : ''}`;
  }
  return `экспозиция экипажа: попаданий ${minutes(hit)}, пропусков ${minutes(miss)}, ложных тревог ${minutes(falseAlarm)}`;
}

// Вердикт сверки одной строкой по полям Verification: предупреждение и ложные тревоги, попадания или
// пропуски, совпадение выбора окна. Каждая часть берётся из полей, не из текста note.
export function verificationVerdict(v: Verification): string {
  const parts: string[] = [];
  if (warnedInAdvance(v)) {
    const lead = v.lead_time_minutes === null ? '' : `: за ${minutes(v.lead_time_minutes)}`;
    const falseAlarms = v.false_alarm_minutes > 0 ? `ложных тревог ${minutes(v.false_alarm_minutes)}` : 'ложных тревог нет';
    parts.push(`Предупреждение выдано заранее${lead}, ${falseAlarms}`);
  } else if (v.first_observed_alarm === null) {
    parts.push(v.observed_availability === 'available' ? 'Факт ≥ high в периоде не наступил' : 'Факта для сверки нет');
  } else if (v.first_predicted_warning === null) {
    parts.push('Предупреждения не было: прогноз не дал уровня ≥ elevated');
  } else {
    parts.push('Предупреждение выдано после первого факта ≥ high');
  }
  const hasExposure = v.exposure_hit_minutes != null;
  if (!hasExposure && v.hit_minutes > 0) parts.push(`попаданий ${minutes(v.hit_minutes)}`);
  if (!hasExposure && v.hit_minutes === 0 && v.miss_minutes > 0) {
    const noFlux = !v.predicted.some((iv) => iv.kind === 'observation');
    parts.push(`уровень high не достигнут${noFlux ? ': измерений потока на момент отсечения не было' : ''}, пропущено ${minutes(v.miss_minutes)}`);
  }
  const exposure = exposureVerdict(v);
  if (exposure) parts.push(exposure);
  if (!v.window_choice_stable) parts.push('после события лучшее окно оказалось иным');
  else if (v.observed_preferred) parts.push('выбор окна совпал с окном по факту');
  return parts.join('; ');
}
