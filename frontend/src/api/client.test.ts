import { afterEach, describe, expect, it } from 'vitest';
import { ApiError, api, errorText, parseError, setFetch, waitJob } from './client';

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

afterEach(() => setFetch((input, init) => fetch(input, init)));

describe('разбор ошибок API', () => {
  it('берёт code и message из {"error": {...}}', async () => {
    const error = await parseError(jsonResponse(409, { error: { code: 'no_trajectory', message: 'нет эфемериды' } }));
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(409);
    expect(error.code).toBe('no_trajectory');
    expect(error.message).toBe('нет эфемериды');
  });

  it('описывает ответ без JSON статусом', async () => {
    const error = await parseError(new Response('<html>', { status: 502, headers: { 'content-type': 'text/html' } }));
    expect(error.code).toBe('http');
    expect(error.message).toBe('HTTP 502');
  });

  it('ошибка валидации 400 доходит до текста для пользователя', async () => {
    setFetch(async () => jsonResponse(400, { error: { code: 'validation', message: 'режим replay требует cutoff' } }));
    await expect(api.createJob({ mode: 'replay', cutoff: null, eva_start: '2024-05-11T04:00:00Z', duration_hours: 6, search_start: '2024-05-11T00:00:00Z', search_end: '2024-05-11T14:00:00Z' })).rejects.toMatchObject({ message: 'режим replay требует cutoff', status: 400 });
    expect(errorText(new TypeError('Failed to fetch'))).toBe('API недоступен');
  });
});

describe('опрос задания', () => {
  it('возвращает задание после done и сообщает прогресс по шагам', async () => {
    const statuses = [
      { id: 'j1', status: 'queued', progress: null },
      { id: 'j1', status: 'running', progress: { step: 'line:sep_protons', fraction: 0.5, at: '2024-05-11T00:00:00Z' } },
      { id: 'j1', status: 'done', progress: { step: 'saved', fraction: 1, at: '2024-05-11T00:00:01Z' }, assessment_id: 'a1' },
    ];
    let call = 0;
    setFetch(async () => jsonResponse(200, statuses[Math.min(call++, statuses.length - 1)]));
    const seen: string[] = [];
    const job = await waitJob('j1', { onProgress: (j) => seen.push(j.progress ? j.progress.step : j.status), sleep: async () => {} });
    expect(job.status).toBe('done');
    expect(job.assessment_id).toBe('a1');
    expect(seen).toEqual(['queued', 'line:sep_protons']);
  });

  it('задание failed возвращается с текстом ошибки, а не выбрасывается', async () => {
    setFetch(async () => jsonResponse(200, { id: 'j2', status: 'failed', progress: null, error: 'NoTrajectoryData: нет эфемериды' }));
    const job = await waitJob('j2', { sleep: async () => {} });
    expect(job.status).toBe('failed');
    expect(job.error).toContain('нет эфемериды');
  });
});
