import type { Availability, Grade, Kind, Level, Mode, SourceStateName } from '../api/client';

export const LINE_ORDER = ['sep_protons', 'proton_access_geometry', 'meteor_showers', 'conjunctions', 'dose_proxy', 'illumination'] as const;

export const LINE_LABEL: Record<string, string> = {
  sep_protons: 'Протоны СЭЧ',
  meteor_showers: 'Метеорные потоки',
  conjunctions: 'Сближения',
  dose_proxy: 'Прокси дозы',
  illumination: 'Освещённость',
  proton_access_geometry: 'Геометрия доступа при Kp 9',
  saa: 'ЮАА по трассе',
};

export const LINE_SHORT: Record<string, string> = {
  sep_protons: 'Космическая погода',
  meteor_showers: 'Метеороидная среда',
  conjunctions: 'Орбитальные сближения',
  dose_proxy: 'Радиационная доза',
  illumination: 'Освещённость',
  proton_access_geometry: 'Геометрия доступа',
};

export const LINE_ICON: Record<string, string> = {
  sep_protons: 'Sun',
  meteor_showers: 'Sparkles',
  conjunctions: 'Satellite',
  dose_proxy: 'Radiation',
  illumination: 'Eclipse',
  proton_access_geometry: 'Compass',
};

export const MECHANISM_LABEL: Record<string, string> = {
  radiation_sep: 'радиация: солнечные энергичные частицы',
  meteoroids: 'метеороиды',
  planning_conflict: 'конфликт планирования',
  radiation_dose_proxy: 'прокси дозы среды',
  work_conditions: 'условия работ',
  radiation_access_geometry: 'геометрия доступа протонов, не прогноз',
};

export const KIND_LABEL: Record<Kind, string> = {
  observation: 'наблюдение',
  external_forecast: 'внешний прогноз',
  computed: 'наш расчёт',
};

export const LEVEL_LABEL: Record<Level, string> = {
  none: 'фон',
  elevated: 'повышенный',
  high: 'высокий',
  critical: 'критический',
  unknown: 'нет данных',
};

// Цвета уровней согласованы с токенами style.css.
export const LEVEL_COLOR: Record<Level, string> = {
  none: '#5bd3bb',
  elevated: '#e8bd70',
  high: '#e87470',
  critical: '#c58af0',
  unknown: '#5c646c',
};

// Плашка строки временной картины: линия, все интервалы которой это внешний прогноз, подписывается
// «внешний прогноз», а не «данные есть»; иначе плашка доступности.
export function lineBadge(line: { availability: Availability; intervals: { kind: Kind }[] }): { kind: Kind } | { availability: Availability } {
  if (line.availability === 'available' && line.intervals.length > 0 && line.intervals.every((iv) => iv.kind === 'external_forecast')) {
    return { kind: 'external_forecast' };
  }
  return { availability: line.availability };
}

export const AVAILABILITY_LABEL: Record<Availability, string> = {
  available: 'данные есть',
  missing: 'нет данных',
  stale: 'данные устарели',
  unusable_for_replay: 'нет архива источника',
};

export const GRADE_LABEL: Record<Grade, string> = {
  low: 'низкая',
  medium: 'средняя',
  high: 'высокая',
};

export const MODE_LABEL: Record<Mode, string> = {
  current: 'сейчас',
  review: 'разбор по архиву',
  replay: 'прогноз из прошлого',
};

export const STATE_LABEL: Record<SourceStateName, string> = {
  active: 'активен',
  frozen: 'заморожен',
  disabled: 'отключён',
};

export const SOURCE_LABEL: Record<string, string> = {
  nasa_oem: 'Эфемерида МКС (NASA OEM)',
  celestrak: 'Орбитальные элементы (CelesTrak GP)',
  swpc_live: 'Космическая погода (NOAA SWPC)',
  donki: 'Уведомления DONKI (NASA)',
  gfz_kp: 'Индекс Kp (GFZ)',
  socrates: 'Сближения (CelesTrak SOCRATES)',
  meteor_docs: 'Метеорные прогнозы (NASA MEO, IMO)',
  goes_sgps: 'Спектры протонов GOES (NCEI)',
  swpc_archive: 'Архив SWPC (NCEI)',
};

export const STEP_LABEL: Record<string, string> = {
  archive: 'архив периода: проверка и докачка',
  trajectory: 'траектория',
  'line:sep_protons': 'линия протонов СЭЧ',
  'line:meteor_showers': 'линия метеорных потоков',
  'line:conjunctions': 'линия сближений',
  'line:dose_proxy': 'линия прокси дозы',
  'line:illumination': 'линия освещённости',
  windows: 'сравнение окон',
  saved: 'сохранение',
};

export const PROGRESS_STEPS: { step: string; fraction: number }[] = [
  { step: 'archive', fraction: 0.1 },
  { step: 'trajectory', fraction: 0.2 },
  { step: 'line:sep_protons', fraction: 0.5 },
  { step: 'line:meteor_showers', fraction: 0.7 },
  { step: 'line:conjunctions', fraction: 0.75 },
  { step: 'line:dose_proxy', fraction: 0.8 },
  { step: 'line:illumination', fraction: 0.85 },
  { step: 'windows', fraction: 0.9 },
  { step: 'saved', fraction: 1.0 },
];

export const lineLabel = (line: string): string => LINE_LABEL[line] || line;
export const sourceLabel = (name: string): string => SOURCE_LABEL[name] || name;
