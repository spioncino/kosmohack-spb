import { describe, expect, it } from 'vitest';
import { CURRENT_PAST_TEXT, NO_TRAJECTORY_ARCHIVE, NO_TRAJECTORY_CURRENT, currentRangeIssue, isNoTrajectoryError, jobErrorText } from './currentMode';

// Сейчас 19 сентября 2026 12:00 UTC; последняя эфемерида NASA покрывает время до 3 октября 12:00 UTC.
const NOW = Date.parse('2026-09-19T12:00:00Z');
const STOP = '2026-10-03T12:00:00Z';
const near = { mode: 'current' as const, evaStart: '2026-09-19T16:00:00Z', searchStart: '2026-09-19T14:00:00Z', searchEnd: '2026-09-20T02:00:00Z' };
// Окно 29 июля 2026 из сообщения об ошибке: задание падало с NoTrajectoryData.
const july = { mode: 'current' as const, evaStart: '2026-07-29T14:00:00Z', searchStart: '2026-07-29T12:00:00Z', searchEnd: '2026-07-29T22:00:00Z' };

describe('даты режима «сейчас»', () => {
  it('ближайшие часы проходят', () => {
    expect(currentRangeIssue(near, NOW, STOP)).toBeNull();
  });

  it('29 июля 2026 при сегодняшнем 19 сентября: прошлое, совет выбрать разбор или прогноз из прошлого', () => {
    const issue = currentRangeIssue(july, NOW, STOP);
    expect(issue?.kind).toBe('past');
    expect(issue?.text).toBe(CURRENT_PAST_TEXT);
    expect(issue?.detail).toContain('2026-07-29 12:00 UTC');
    expect(issue?.detail).toContain('2026-09-18 12:00 UTC');
  });

  it('граница прошлого сутки до текущего момента включительно; проверяется и начало ВКД, и начало периода поиска', () => {
    const edge = { ...near, evaStart: '2026-09-18T14:00:00Z', searchStart: '2026-09-18T12:00:00Z', searchEnd: '2026-09-19T00:00:00Z' };
    expect(currentRangeIssue(edge, NOW, STOP)).toBeNull();
    expect(currentRangeIssue({ ...edge, searchStart: '2026-09-18T11:59:00Z' }, NOW, STOP)?.kind).toBe('past');
    expect(currentRangeIssue({ ...edge, evaStart: '2026-09-18T11:45:00Z' }, NOW, STOP)?.kind).toBe('past');
  });

  it('разбор и прогноз из прошлого не ограничиваются', () => {
    expect(currentRangeIssue({ ...july, mode: 'review' }, NOW, STOP)).toBeNull();
    expect(currentRangeIssue({ ...july, mode: 'replay' }, NOW, STOP)).toBeNull();
  });

  it('будущее: период поиска с часом запаса должен закончиться не позже конца эфемериды', () => {
    const late = { ...near, evaStart: '2026-10-03T02:00:00Z', searchStart: '2026-10-03T00:00:00Z', searchEnd: '2026-10-03T11:00:00Z' };
    expect(currentRangeIssue(late, NOW, STOP)).toBeNull();
    const beyond = currentRangeIssue({ ...late, searchEnd: '2026-10-03T11:15:00Z' }, NOW, STOP);
    expect(beyond?.kind).toBe('future');
    expect(beyond?.text).toContain('2026-10-03 12:00 UTC');
    expect(beyond?.detail).toContain('не позже 2026-10-03 11:00 UTC');
  });

  it('без покрытия эфемериды из API будущее не проверяется', () => {
    const far = { ...near, evaStart: '2026-12-01T02:00:00Z', searchStart: '2026-12-01T00:00:00Z', searchEnd: '2026-12-01T12:00:00Z' };
    expect(currentRangeIssue(far, NOW, null)).toBeNull();
    expect(currentRangeIssue(far, NOW)).toBeNull();
    expect(currentRangeIssue(far, NOW, STOP)?.kind).toBe('future');
  });

  it('пустые и неразобранные даты не дают предупреждения: их называет проверка формы', () => {
    expect(currentRangeIssue({ mode: 'current', evaStart: null, searchStart: null, searchEnd: null }, NOW, STOP)).toBeNull();
    expect(currentRangeIssue({ mode: 'current', evaStart: 'x', searchStart: null, searchEnd: null }, NOW, 'y')).toBeNull();
  });
});

describe('ошибка задания без эфемериды МКС', () => {
  const raw = 'NoTrajectoryData: нет эфемериды МКС, доступной на момент запроса и покрывающей период с запасом в час';

  it('распознаётся по имени исключения и по тексту API', () => {
    expect(isNoTrajectoryError(raw)).toBe(true);
    expect(isNoTrajectoryError('нет эфемериды МКС, доступной на момент запроса')).toBe(true);
    expect(isNoTrajectoryError('ValueError: что-то иное')).toBe(false);
  });

  it('в режиме «сейчас» и без режима совет выбрать разбор или прогноз из прошлого', () => {
    expect(jobErrorText(raw, 'current')).toBe(NO_TRAJECTORY_CURRENT);
    expect(jobErrorText(raw)).toBe(NO_TRAJECTORY_CURRENT);
    expect(NO_TRAJECTORY_CURRENT).toContain(CURRENT_PAST_TEXT);
    expect(NO_TRAJECTORY_CURRENT).not.toContain('NoTrajectoryData');
  });

  it('в разборе и прогнозе из прошлого речь об архиве сервера', () => {
    expect(jobErrorText(raw, 'review')).toBe(NO_TRAJECTORY_ARCHIVE);
    expect(jobErrorText(raw, 'replay')).toBe(NO_TRAJECTORY_ARCHIVE);
  });

  it('прочие ошибки показываются как есть', () => {
    expect(jobErrorText('API недоступен', 'current')).toBe('API недоступен');
  });
});
