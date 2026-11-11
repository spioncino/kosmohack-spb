import { describe, expect, it } from 'vitest';
import { keepsBriefing, latestGuard } from './latest';

describe('latestGuard', () => {
  it('отбрасывает ответ на ранний запрос, пришедший после позднего', async () => {
    const guard = latestGuard();
    const shown: string[] = [];
    let releaseSlow: (value: string) => void = () => undefined;
    const slow = new Promise<string>((resolve) => { releaseSlow = resolve; });
    const load = async (response: Promise<string>): Promise<void> => {
      const request = guard.begin();
      const value = await response;
      if (guard.isCurrent(request)) shown.push(value);
    };
    // Сначала открыт разбор 11 мая, сразу за ним прогноз из прошлого; сводка разбора отвечает последней.
    const first = load(slow);
    const second = load(Promise.resolve('прогноз из прошлого b5645fd1d123'));
    await second;
    releaseSlow('разбор 6d50ec81bae3');
    await first;
    expect(shown).toEqual(['прогноз из прошлого b5645fd1d123']);
  });
});

describe('keepsBriefing', () => {
  it('сводка другого расчёта не остаётся на экране', () => {
    expect(keepsBriefing({ assessment_id: '6d50ec81bae3' }, 'b5645fd1d123')).toBe(false);
    expect(keepsBriefing({ assessment_id: 'b5645fd1d123' }, 'b5645fd1d123')).toBe(true);
    expect(keepsBriefing(null, 'b5645fd1d123')).toBe(false);
  });
});
