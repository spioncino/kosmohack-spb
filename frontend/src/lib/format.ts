// Форматирование времени: все значения показываются в UTC с явной пометкой.

// Строка ISO без зоны (EPOCH в OMM CelesTrak) трактуется как UTC, а не как локальное время браузера.
const NO_ZONE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/;

export function parseUtc(value: string | number | Date): Date {
  if (typeof value === 'string' && NO_ZONE.test(value)) return new Date(`${value}Z`);
  return new Date(value);
}

export const ms = (iso: string | number | Date): number => parseUtc(iso).getTime();

function pad(value: number): string {
  return String(value).padStart(2, '0');
}

// «2024-05-11 04:00 UTC»; секунды добавляются по запросу.
export function fmtUtc(value: string | number | Date | null | undefined, seconds = false): string {
  if (value === null || value === undefined || value === '') return '—';
  const date = parseUtc(value);
  if (Number.isNaN(date.getTime())) return '—';
  const base = `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())} ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}`;
  return (seconds ? `${base}:${pad(date.getUTCSeconds())}` : base) + ' UTC';
}

// Окно «2024-05-11 09:30–15:30 UTC»; у окна через полночь дата конца указана полностью.
export function fmtWindow(window: { start: string; end: string }): string {
  const start = fmtUtc(window.start).replace(/ UTC$/, '');
  const end = fmtUtc(window.end).replace(/ UTC$/, '');
  return start.slice(0, 10) === end.slice(0, 10) ? `${start}–${end.slice(11)} UTC` : `${start} – ${end} UTC`;
}

// Только часы и минуты «04:00»; для подписей осей и таймлайна.
export function fmtTime(value: string | number | Date, seconds = false): string {
  const date = parseUtc(value);
  const base = `${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}`;
  return seconds ? `${base}:${pad(date.getUTCSeconds())}` : base;
}

// «11 мая 2024» по UTC.
export function fmtDate(value: string | number | Date): string {
  return parseUtc(value).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' });
}

// ISO без миллисекунд и с Z: значение для запросов к API.
export function isoZ(value: Date | number | string): string {
  return parseUtc(value).toISOString().replace(/\.\d{3}Z$/, 'Z');
}

// Значение для input[type=datetime-local] в UTC (без зоны): «2024-05-11T04:00».
export function toLocalInput(value: Date | number | string): string {
  return parseUtc(value).toISOString().slice(0, 16);
}

// Обратное преобразование: строка из datetime-local трактуется как UTC.
export function fromLocalInput(value: string): string | null {
  if (!value) return null;
  const date = new Date(value.length === 16 ? `${value}:00Z` : `${value}Z`);
  return Number.isNaN(date.getTime()) ? null : isoZ(date);
}

// Давность относительно now: «только что», «5 мин назад», «3 ч назад», «2 дн назад».
export function ago(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return 'никогда';
  const minutes = Math.floor((now - ms(iso)) / 60000);
  if (minutes < 1) return 'только что';
  if (minutes < 60) return `${minutes} мин назад`;
  if (minutes < 48 * 60) return `${Math.floor(minutes / 60)} ч назад`;
  return `${Math.floor(minutes / 1440)} дн назад`;
}

// Период опроса источника: «5 мин», «6 ч», «30 дн».
export function cadence(seconds: number): string {
  if (seconds >= 86400) {
    const days = seconds / 86400;
    return `${Number.isInteger(days) ? days : days.toFixed(1)} дн`;
  }
  if (seconds >= 3600) {
    const hours = seconds / 3600;
    return `${Number.isInteger(hours) ? hours : hours.toFixed(1)} ч`;
  }
  return `${Math.round(seconds / 60)} мин`;
}

export function hoursBetween(start: string | number, end: string | number): number {
  return (ms(end) - ms(start)) / 3600000;
}

export function minutesLabel(value: number | null | undefined): string {
  return value === null || value === undefined ? 'не определена' : `${Math.round(value)} мин`;
}

export function num(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  return value.toLocaleString('ru-RU', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function percent(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  return `${(value * 100).toFixed(digits)} %`;
}
