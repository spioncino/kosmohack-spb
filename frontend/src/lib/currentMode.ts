import type { Mode } from '../api/client';
import { fmtUtc, ms } from './format';

// Даты, которые может рассчитать режим «сейчас» (D87). Расчёт «сейчас» берёт эфемериду МКС, доступную на момент
// запроса и покрывающую период поиска с запасом в час (src/evarisk/store/views.py: oem_for, NO_TRAJECTORY_MESSAGE в
// src/evarisk/api/service.py). Текущая эфемерида NASA покрывает недавнее прошлое и ближайшие дни, поэтому для
// прошлых дат нужен «Разбор по архиву» или «Прогноз из прошлого», а запрос с такими датами форма не отправляет.
//
// Прошлое: API не отдаёт, насколько назад хватает эфемерид в хранилище (oem_for перебирает все доступные записи),
// поэтому граница это сутки до текущего момента, как у переноса окна при выборе режима (EvaView.vue: onModeChange).
// Будущее: GET /orbit/latest отдаёт stop_time последней эфемериды; период поиска вместе с часом запаса должен
// закончиться не позже него. Без этого поля будущее не проверяется.

const HOUR_MS = 3600000;
export const CURRENT_PAST_HOURS = 24;
export const TRAJECTORY_MARGIN_HOURS = 1;

export const CURRENT_PAST_TEXT =
  'В режиме «Сейчас» доступны только ближайшие дни: для прошлых дат выберите «Разбор по архиву» или «Прогноз из прошлого»';

export interface CurrentRangeInput {
  mode: Mode;
  evaStart: string | null;
  searchStart: string | null;
  searchEnd: string | null;
}

export interface CurrentRangeIssue {
  // past: дата раньше суток до текущего момента, помогает смена режима; future: дальше покрытия эфемериды.
  kind: 'past' | 'future';
  text: string;
  detail: string;
}

function valid(value: string | null): number | null {
  if (!value) return null;
  const t = ms(value);
  return Number.isNaN(t) ? null : t;
}

// null, если режим не «сейчас» или даты в пределах, которые он может рассчитать. now в миллисекундах;
// coverageEnd это stop_time последней эфемериды из GET /orbit/latest или null, если его нет.
export function currentRangeIssue(input: CurrentRangeInput, now: number, coverageEnd?: string | null): CurrentRangeIssue | null {
  if (input.mode !== 'current') return null;
  const starts = [valid(input.evaStart), valid(input.searchStart)].filter((t): t is number => t !== null);
  const limit = now - CURRENT_PAST_HOURS * HOUR_MS;
  if (starts.length) {
    const earliest = Math.min(...starts);
    if (earliest < limit) {
      return {
        kind: 'past',
        text: CURRENT_PAST_TEXT,
        detail: `Начало ${fmtUtc(earliest)} раньше ${fmtUtc(limit)}: режим «Сейчас» рассчитывает окна не раньше суток до текущего момента.`,
      };
    }
  }
  const stop = valid(coverageEnd ?? null);
  const ends = [valid(input.searchEnd), valid(input.evaStart)].filter((t): t is number => t !== null);
  if (stop !== null && ends.length) {
    const latest = Math.max(...ends);
    const lastEnd = stop - TRAJECTORY_MARGIN_HOURS * HOUR_MS;
    if (latest > lastEnd) {
      return {
        kind: 'future',
        text: `В режиме «Сейчас» доступны только ближайшие дни: последняя эфемерида МКС покрывает время до ${fmtUtc(stop)}`,
        detail: `Период поиска заканчивается ${fmtUtc(latest)}, а с запасом в час, который нужен расчёту, он должен закончиться не позже ${fmtUtc(lastEnd)}. Выберите более раннее окно.`,
      };
    }
  }
  return null;
}

// Задание упало без эфемериды МКС (NoTrajectoryData из src/evarisk/api/service.py): вместо текста исключения
// понятная фраза с тем же советом, что у проверки формы. Остальные ошибки возвращаются как есть. mode это режим
// запроса упавшего задания (Job.params.mode); без него совет дан как для режима «сейчас».
export const NO_TRAJECTORY_CURRENT = `Для выбранных дат нет орбиты МКС, без неё расчёт невозможен. ${CURRENT_PAST_TEXT}.`;
export const NO_TRAJECTORY_ARCHIVE =
  'Для выбранных дат в архиве сервера нет орбиты МКС, без неё расчёт невозможен. Выберите даты, которые покрывает архив, или режим «Сейчас» для ближайших дней.';

export function isNoTrajectoryError(raw: string): boolean {
  return raw.includes('NoTrajectoryData') || raw.includes('нет эфемериды МКС');
}

export function jobErrorText(raw: string, mode?: unknown): string {
  if (!isNoTrajectoryError(raw)) return raw;
  return mode === 'review' || mode === 'replay' ? NO_TRAJECTORY_ARCHIVE : NO_TRAJECTORY_CURRENT;
}
