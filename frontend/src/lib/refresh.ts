import type { Mode, SourceState } from '../api/client';
import { ms } from './format';

// Автообновление открытого расчёта в режиме «сейчас» (постановка: в режиме текущей обстановки данные обновляются
// автоматически с учётом частоты публикации источников). Тот же запрос пересчитывается заново, когда воркер получил
// новые данные после расчёта, но не чаще раза в REFRESH_MIN_GAP_MS; без новых данных не реже раза в REFRESH_EVERY_MS.
// Разбор и прогноз из прошлого не пересчитываются: их данные зафиксированы моментом отсечения или архивом.

// Самый частый источник SWPC опрашивается раз в 5 мин: чаще пересчитывать нечего.
export const REFRESH_MIN_GAP_MS = 5 * 60000;
export const REFRESH_EVERY_MS = 10 * 60000;

export interface RefreshInput {
  mode: Mode | null | undefined;
  // Момент расчёта открытого результата (created_at); null без расчёта.
  computedAt: string | null | undefined;
  now: number;
  // Вкладка браузера видна: в скрытой вкладке пересчёт не ставится.
  visible: boolean;
  // Идёт задание или загрузка расчёта.
  busy: boolean;
  // Самое позднее успешное получение данных активным источником (newestSuccess).
  newestData: string | null;
  // Момент последней автоматической постановки задания; после неудачи повтор не раньше чем через REFRESH_MIN_GAP_MS.
  lastAttempt: number | null;
}

export type RefreshReason = 'new_data' | 'age';

export interface RefreshPlan {
  // Пересчёт ставится сейчас.
  due: boolean;
  // Почему пересчёт нужен к моменту next: новые данные источников или возраст расчёта; null вне режима «сейчас».
  reason: RefreshReason | null;
  // Не позже этого момента расчёт будет пересчитан при видимой вкладке; null вне режима «сейчас».
  next: number | null;
}

const NOT_CURRENT: RefreshPlan = { due: false, reason: null, next: null };

export function refreshPlan(input: RefreshInput): RefreshPlan {
  if (input.mode !== 'current' || !input.computedAt) return NOT_CURRENT;
  const computed = ms(input.computedAt);
  if (Number.isNaN(computed)) return NOT_CURRENT;
  const earliest = Math.max(computed, input.lastAttempt ?? computed) + REFRESH_MIN_GAP_MS;
  const newData = input.newestData !== null && ms(input.newestData) > computed;
  const next = newData ? earliest : Math.max(computed + REFRESH_EVERY_MS, earliest);
  return { due: input.visible && !input.busy && input.now >= next, reason: newData ? 'new_data' : 'age', next };
}

// Самое позднее last_success_at среди активных источников; null, если успешных получений нет.
export function newestSuccess(sources: Pick<SourceState, 'state' | 'last_success_at'>[]): string | null {
  let best: string | null = null;
  for (const source of sources) {
    if (source.state !== 'active' || !source.last_success_at) continue;
    if (best === null || ms(source.last_success_at) > ms(best)) best = source.last_success_at;
  }
  return best;
}
