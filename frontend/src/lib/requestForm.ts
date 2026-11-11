import type { AssessmentRequest, Mode } from '../api/client';
import { fmtUtc, fromLocalInput, ms, toLocalInput } from './format';
import { MODE_LABEL } from './labels';
import { startPeriodOf } from './period';

// Поля формы расчёта на /eva: времена в виде input[type=datetime-local] (UTC), период допустимого начала началом и
// длиной в часах, доля света строкой (пустая строка это «не задано»).
export interface RequestForm {
  mode: Mode;
  cutoff: string;
  evaStart: string;
  duration: number;
  startFrom: string;
  startHours: number;
  minSunlit: string;
  startInDaylight: boolean;
}

// Форма ровно по запросу открытого расчёта: повторный запуск без правок даёт тот же запрос. Длина периода допустимого
// начала берётся точно (search_end − duration_hours − search_start, в целых минутах), а не округляется до шага поля.
export function formFromRequest(request: AssessmentRequest): RequestForm {
  const period = startPeriodOf(request);
  const sunlit = request.constraints?.min_sunlit_fraction;
  return {
    mode: request.mode,
    cutoff: request.cutoff ? toLocalInput(request.cutoff) : '',
    evaStart: toLocalInput(request.eva_start),
    duration: request.duration_hours,
    startFrom: toLocalInput(request.search_start),
    startHours: Math.round(period.hours * 60) / 60,
    minSunlit: sunlit === null || sunlit === undefined ? '' : String(sunlit),
    startInDaylight: !!request.constraints?.start_in_daylight,
  };
}

// Выбранное время совпадает с началом открытого расчёта: так его ставит открытие расчёта (store/state.ts: focusOn), и
// форма, уже показывающая запрос расчёта, от этого не сдвигается.
export function isOpenedStart(selected: Date | number, request: Pick<AssessmentRequest, 'eva_start'> | null | undefined, windowStart?: string | null): boolean {
  if (!request) return false;
  const at = typeof selected === 'number' ? selected : selected.getTime();
  return at === ms(request.eva_start) || (!!windowStart && at === ms(windowStart));
}

// Шаг поля длины периода: 0,25 ч, если длина кратна четверти часа, иначе любой, чтобы браузер не отверг точную длину.
export function startHoursStep(hours: number): number | 'any' {
  return Math.abs(hours * 4 - Math.round(hours * 4)) < 1e-9 ? 0.25 : 'any';
}

// Сравнение формы с запросом показанного расчёта (D85) в тех же терминах, что formFromRequest: форма без правок
// расходится с запросом ни в чём. Времена сравниваются как моменты UTC, длина периода в целых минутах, доля света
// числом (запятая как точка, пустая строка это «не задано»), отсечение только когда режим прогноза из прошлого
// у обоих. Результат: короткие строки «что было → что в форме» в порядке полей формы; пустой список, если форма
// показывает ровно запрос расчёта.
function instant(value: string): number | null {
  const iso = fromLocalInput(value);
  return iso ? ms(iso) : null;
}

function instantText(value: string): string {
  const iso = fromLocalInput(value);
  return iso ? fmtUtc(iso).replace(/ UTC$/, '') : 'не задано';
}

function formMinutes(hours: number | string): number {
  return typeof hours === 'string' && hours.trim() === '' ? Number.NaN : Math.round(Number(hours) * 60);
}

function hoursLabel(hours: number | string): string {
  const minutes = formMinutes(hours);
  if (!Number.isFinite(minutes)) return 'не задано';
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (!m) return `${h} ч`;
  return h ? `${h} ч ${m} мин` : `${m} мин`;
}

function sunlitValue(value: string): number | null {
  const text = String(value ?? '').trim();
  return text === '' ? null : Number(text.replace(',', '.'));
}

function sunlitText(value: string): string {
  const text = String(value ?? '').trim();
  return text === '' ? 'не задано' : text;
}

export function formDifferences(form: RequestForm, request: AssessmentRequest): string[] {
  const shown = formFromRequest(request);
  const out: string[] = [];
  if (form.mode !== shown.mode) out.push(`режим: ${MODE_LABEL[shown.mode]} → ${MODE_LABEL[form.mode] ?? form.mode}`);
  else if (form.mode === 'replay' && instant(form.cutoff) !== instant(shown.cutoff)) out.push(`отсечение ${instantText(shown.cutoff)} → ${instantText(form.cutoff)}`);
  if (instant(form.evaStart) !== instant(shown.evaStart)) out.push(`начало ВКД ${instantText(shown.evaStart)} → ${instantText(form.evaStart)}`);
  if (formMinutes(form.duration) !== formMinutes(shown.duration)) out.push(`длительность ${hoursLabel(shown.duration)} → ${hoursLabel(form.duration)}`);
  if (instant(form.startFrom) !== instant(shown.startFrom)) out.push(`начало периода допустимого начала ${instantText(shown.startFrom)} → ${instantText(form.startFrom)}`);
  if (formMinutes(form.startHours) !== formMinutes(shown.startHours)) out.push(`длина периода допустимого начала ${hoursLabel(shown.startHours)} → ${hoursLabel(form.startHours)}`);
  if (sunlitValue(form.minSunlit) !== sunlitValue(shown.minSunlit)) out.push(`доля света ${sunlitText(shown.minSunlit)} → ${sunlitText(form.minSunlit)}`);
  if (!!form.startInDaylight !== shown.startInDaylight) out.push(`старт на свету: ${shown.startInDaylight ? 'да' : 'нет'} → ${form.startInDaylight ? 'да' : 'нет'}`);
  return out;
}

// Пометка над показанным результатом, когда он не отвечает форме или идёт новый расчёт (D85). running это задание,
// поставленное пользователем; пересчёт автообновлением того же запроса сюда не входит. null, если результат текущий.
export interface StaleNotice {
  // Главная строка баннера.
  title: string;
  // Что именно разошлось, когда идёт расчёт и форма тоже изменена; иначе null.
  detail: string | null;
  // Что сделать, чтобы результат снова отвечал форме.
  hint: string;
  running: boolean;
}

// action: подпись кнопки запуска на этом экране.
export function staleNotice(differences: string[], running: boolean, action = 'Рассчитать'): StaleNotice | null {
  const differs = differences.length ? `Результат относится к предыдущим параметрам: ${differences.join('; ')}` : null;
  if (running) return { title: 'Идёт новый расчёт, ниже прежний результат', detail: differs, hint: 'Новый результат заменит прежний, когда задание завершится.', running };
  if (!differs) return null;
  return { title: differs, detail: null, hint: `Нажмите «${action}», чтобы получить результат для новых параметров, или верните параметры показанного расчёта.`, running };
}
