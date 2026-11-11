import { describe, expect, it } from 'vitest';
import type { AssessmentRequest } from '../api/client';
import { MAX_START_HOURS, MIN_START_MINUTES, START_OUTSIDE_ERROR, START_SPAN_ERROR, periodCaption, searchPeriodFrom, startPeriodError, startPeriodOf, startPeriodText, windowsSpanText } from './period';

// Прогноз из прошлого на 11 мая 2024: старт 04:00, 6 ч, период поиска сутки 00:00–24:00.
const replay: AssessmentRequest = { mode: 'replay', cutoff: '2024-05-10T12:30:00Z', eva_start: '2024-05-11T04:00:00Z', duration_hours: 6, search_start: '2024-05-11T00:00:00Z', search_end: '2024-05-12T00:00:00Z' };

describe('период допустимого начала и период поиска', () => {
  it('из запроса: допустимое начало от search_start до search_end минус длительность', () => {
    expect(startPeriodOf(replay)).toEqual({ start: '2024-05-11T00:00:00Z', end: '2024-05-11T18:00:00Z', hours: 18 });
    expect(startPeriodOf({ search_start: '2024-05-10T12:00:00Z', search_end: '2024-05-10T22:00:00Z', duration_hours: 6 }).hours).toBe(4);
  });

  it('в запрос: search_end это начало плюс длина периода допустимого начала плюс длительность', () => {
    expect(searchPeriodFrom('2024-05-11T00:00:00Z', 18, 6)).toEqual({ search_start: '2024-05-11T00:00:00Z', search_end: '2024-05-12T00:00:00Z' });
    expect(searchPeriodFrom('2024-05-11T00:00:00Z', 0.25, 1.5)).toEqual({ search_start: '2024-05-11T00:00:00Z', search_end: '2024-05-11T01:45:00Z' });
  });

  it('три примера формы дают прежние запросы: 4, 18 и 6 ч допустимого начала при 6 ч ВКД', () => {
    expect(searchPeriodFrom('2024-05-10T12:00:00Z', 4, 6)).toEqual({ search_start: '2024-05-10T12:00:00Z', search_end: '2024-05-10T22:00:00Z' });
    expect(searchPeriodFrom('2024-05-11T00:00:00Z', 18, 6)).toEqual({ search_start: replay.search_start, search_end: replay.search_end });
    expect(searchPeriodFrom('2026-09-19T14:00:00Z', 6, 6)).toEqual({ search_start: '2026-09-19T14:00:00Z', search_end: '2026-09-20T02:00:00Z' });
  });

  it('преобразование туда и обратно возвращает тот же период поиска', () => {
    for (const request of [replay, { ...replay, duration_hours: 2.5, search_end: '2024-05-11T09:45:00Z' }, { ...replay, duration_hours: 8, search_end: '2024-05-12T08:00:00Z' }]) {
      const period = startPeriodOf(request);
      expect(searchPeriodFrom(period.start, period.hours, request.duration_hours)).toEqual({ search_start: request.search_start, search_end: request.search_end });
    }
  });
});

describe('проверка как в API', () => {
  const at = (startHours: number, duration: number, evaStart: string) => ({ ...searchPeriodFrom('2024-05-11T00:00:00Z', startHours, duration), duration_hours: duration, eva_start: evaStart });

  it('отрезок начал от 15 минут до 24 часов включительно', () => {
    expect(MIN_START_MINUTES).toBe(15);
    expect(MAX_START_HOURS).toBe(24);
    expect(startPeriodError(at(0.25, 6, '2024-05-11T00:00:00Z'))).toBeNull();
    expect(startPeriodError(at(14 / 60, 6, '2024-05-11T00:00:00Z'))).toBe(START_SPAN_ERROR);
    expect(startPeriodError(at(0, 6, '2024-05-11T00:00:00Z'))).toBe(START_SPAN_ERROR);
    expect(startPeriodError(at(24, 8, '2024-05-11T04:00:00Z'))).toBeNull();
    expect(startPeriodError(at(24 + 1 / 60, 8, '2024-05-11T04:00:00Z'))).toBe(START_SPAN_ERROR);
    // Период поиска 00:00–24:00 при 6 ч это 18 ч допустимого начала: проходит, хотя прежняя форма считала сутки поиска пределом.
    expect(startPeriodError(replay)).toBeNull();
  });

  it('начало ВКД внутри периода допустимого начала, границы включены', () => {
    expect(startPeriodError(at(18, 6, '2024-05-11T00:00:00Z'))).toBeNull();
    expect(startPeriodError(at(18, 6, '2024-05-11T18:00:00Z'))).toBeNull();
    expect(startPeriodError(at(18, 6, '2024-05-11T18:01:00Z'))).toBe(`${START_OUTSIDE_ERROR}: 2024-05-11 00:00–18:00 UTC`);
    expect(startPeriodError(at(18, 6, '2024-05-10T23:59:00Z'))).toBe(`${START_OUTSIDE_ERROR}: 2024-05-11 00:00–18:00 UTC`);
  });

  it('пустая дата даёт просьбу заполнить поля, а не ошибку отрезка', () => {
    expect(startPeriodError({ ...replay, eva_start: '' })).toBe('Заполните дату и время начала ВКД и периода допустимого начала');
  });
});

describe('подписи периода', () => {
  it('допустимое начало и отрезок окон', () => {
    expect(startPeriodText(replay)).toBe('допустимое начало 2024-05-11 00:00–18:00 UTC');
    expect(windowsSpanText(replay)).toBe('окна 2024-05-11 00:00 – 2024-05-12 00:00 UTC');
    expect(periodCaption(replay)).toBe('допустимое начало 2024-05-11 00:00–18:00 UTC · окна 2024-05-11 00:00 – 2024-05-12 00:00 UTC');
  });
});
