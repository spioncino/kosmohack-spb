import { describe, expect, it } from 'vitest';
import type { AssessmentRequest } from '../api/client';
import { fromLocalInput } from './format';
import { searchPeriodFrom } from './period';
import { formDifferences, formFromRequest, isOpenedStart, staleNotice, startHoursStep } from './requestForm';

// Запрос «сейчас» 19 сентября 2026: старт 18:00, 6 ч, допустимое начало 16:00–22:00 (период поиска 16:00–04:00).
// Выбранное время открытия расчёта 18:00 раньше сдвигало начало периода на 15:00 (середина периода, не дальше 4 ч).
const current: AssessmentRequest = { mode: 'current', cutoff: null, eva_start: '2026-09-19T18:00:00Z', duration_hours: 6, search_start: '2026-09-19T16:00:00Z', search_end: '2026-09-20T04:00:00Z', constraints: null };
// Прогноз из прошлого на 11 мая 2024 с отсечением 10 мая 12:30 и ограничениями освещённости.
const replay: AssessmentRequest = {
  mode: 'replay', cutoff: '2024-05-10T12:30:00Z', eva_start: '2024-05-11T04:00:00Z', duration_hours: 6, search_start: '2024-05-11T00:00:00Z', search_end: '2024-05-12T00:00:00Z',
  constraints: { min_sunlit_fraction: 0.99, start_in_daylight: true },
};

describe('форма по запросу открытого расчёта', () => {
  it('режим, отсечение, старт, длительность, период допустимого начала и ограничения ровно как в запросе', () => {
    expect(formFromRequest(current)).toEqual({ mode: 'current', cutoff: '', evaStart: '2026-09-19T18:00', duration: 6, startFrom: '2026-09-19T16:00', startHours: 6, minSunlit: '', startInDaylight: false });
    expect(formFromRequest(replay)).toEqual({ mode: 'replay', cutoff: '2024-05-10T12:30', evaStart: '2024-05-11T04:00', duration: 6, startFrom: '2024-05-11T00:00', startHours: 18, minSunlit: '0.99', startInDaylight: true });
  });

  it('доля света 0 это заданное значение, а не «не задано»', () => {
    expect(formFromRequest({ ...current, constraints: { min_sunlit_fraction: 0, start_in_daylight: false } }).minSunlit).toBe('0');
  });

  it('длина периода не округляется до шага поля: 5 ч 50 мин остаются 5 ч 50 мин', () => {
    const odd = { ...current, search_end: '2026-09-20T03:50:00Z' };
    const form = formFromRequest(odd);
    expect(form.startHours * 60).toBeCloseTo(350, 9);
    expect(startHoursStep(form.startHours)).toBe('any');
    expect(startHoursStep(6)).toBe(0.25);
    expect(startHoursStep(0.75)).toBe(0.25);
  });

  it('форма без правок даёт тот же период поиска, что у расчёта', () => {
    for (const request of [current, replay, { ...current, search_end: '2026-09-20T03:50:00Z' }, { ...current, duration_hours: 2.5, search_end: '2026-09-19T22:30:00Z' }]) {
      const form = formFromRequest(request);
      expect(searchPeriodFrom(fromLocalInput(form.startFrom)!, form.startHours, form.duration)).toEqual({ search_start: request.search_start, search_end: request.search_end });
      expect(fromLocalInput(form.evaStart)).toBe(request.eva_start);
    }
  });
});

describe('выбранное время открытия расчёта', () => {
  it('совпадает с началом ВКД или началом запрошенного окна', () => {
    expect(isOpenedStart(new Date('2026-09-19T18:00:00Z'), current)).toBe(true);
    expect(isOpenedStart(Date.parse('2026-09-19T18:00:00Z'), current, '2026-09-19T18:00:00Z')).toBe(true);
    expect(isOpenedStart(new Date('2026-09-19T18:05:00Z'), current)).toBe(false);
    expect(isOpenedStart(new Date('2026-09-19T18:00:00Z'), null)).toBe(false);
  });
});

describe('форма расходится с показанным расчётом (D85)', () => {
  it('форма, заполненная по запросу, не расходится ни в чём, в том числе при длине периода 5 ч 50 мин', () => {
    for (const request of [current, replay, { ...current, search_end: '2026-09-20T03:50:00Z' }, { ...current, constraints: { min_sunlit_fraction: 0, start_in_daylight: false } }]) {
      expect(formDifferences(formFromRequest(request), request)).toEqual([]);
    }
  });

  it('старт 8 июня под результатом 11 мая называется коротко: что было и что в форме', () => {
    const form = { ...formFromRequest(replay), evaStart: '2024-06-08T04:00', startFrom: '2024-06-08T00:00' };
    expect(formDifferences(form, replay)).toEqual([
      'начало ВКД 2024-05-11 04:00 → 2024-06-08 04:00',
      'начало периода допустимого начала 2024-05-11 00:00 → 2024-06-08 00:00',
    ]);
  });

  it('режим, отсечение, длительность, длина периода и ограничения', () => {
    const base = formFromRequest(replay);
    expect(formDifferences({ ...base, mode: 'review' }, replay)).toEqual(['режим: прогноз из прошлого → разбор по архиву']);
    expect(formDifferences({ ...base, cutoff: '2024-05-10T18:00' }, replay)).toEqual(['отсечение 2024-05-10 12:30 → 2024-05-10 18:00']);
    expect(formDifferences({ ...base, duration: 4 }, replay)).toEqual(['длительность 6 ч → 4 ч']);
    expect(formDifferences({ ...base, startHours: 4.5 }, replay)).toEqual(['длина периода допустимого начала 18 ч → 4 ч 30 мин']);
    expect(formDifferences({ ...base, minSunlit: '' }, replay)).toEqual(['доля света 0.99 → не задано']);
    expect(formDifferences({ ...base, startInDaylight: false }, replay)).toEqual(['старт на свету: да → нет']);
  });

  it('сравнение в тех же терминах, что форма: моменты, минуты, числа', () => {
    const base = formFromRequest(replay);
    // Секунды в поле времени, «0,99» с запятой и длина строкой из поля ввода не меняют запрос.
    expect(formDifferences({ ...base, evaStart: '2024-05-11T04:00:00', minSunlit: '0,990', startHours: '18' as unknown as number }, replay)).toEqual([]);
    // Отсечение вне режима прогноза из прошлого в запрос не уходит и расхождением не считается.
    expect(formDifferences({ ...formFromRequest(current), cutoff: '2024-05-10T12:30' }, current)).toEqual([]);
    // Очищенное поле это расхождение, а не совпадение.
    expect(formDifferences({ ...base, evaStart: '' }, replay)).toEqual(['начало ВКД 2024-05-11 04:00 → не задано']);
    expect(formDifferences({ ...base, startHours: '' as unknown as number }, replay)).toEqual(['длина периода допустимого начала 18 ч → не задано']);
  });

  it('возврат формы к запросу снимает расхождение', () => {
    const changed = { ...formFromRequest(replay), evaStart: '2024-06-08T04:00' };
    expect(formDifferences(changed, replay)).toHaveLength(1);
    expect(formDifferences({ ...changed, evaStart: '2024-05-11T04:00' }, replay)).toEqual([]);
  });
});

describe('баннер над прежним результатом (D85)', () => {
  it('без расхождений и без задания пользователя баннера нет', () => {
    expect(staleNotice([], false)).toBeNull();
  });

  it('изменённая форма: заголовок называет, что разошлось', () => {
    const notice = staleNotice(['начало ВКД 2024-05-11 04:00 → 2024-06-08 04:00', 'длительность 6 ч → 4 ч'], false);
    expect(notice?.title).toBe('Результат относится к предыдущим параметрам: начало ВКД 2024-05-11 04:00 → 2024-06-08 04:00; длительность 6 ч → 4 ч');
    expect(notice?.detail).toBeNull();
    expect(notice?.running).toBe(false);
    expect(notice?.hint).toContain('«Рассчитать»');
    expect(staleNotice(['длительность 6 ч → 4 ч'], false, 'Пересчитать')?.hint).toContain('«Пересчитать»');
  });

  it('идёт расчёт: прежний результат помечен, расхождение формы строкой ниже', () => {
    expect(staleNotice([], true)).toMatchObject({ title: 'Идёт новый расчёт, ниже прежний результат', detail: null, running: true });
    expect(staleNotice(['длительность 6 ч → 4 ч'], true)?.detail).toBe('Результат относится к предыдущим параметрам: длительность 6 ч → 4 ч');
  });
});
