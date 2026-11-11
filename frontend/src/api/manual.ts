import { parseError } from './client';

// Раздел «Документация»: GET /manual (список документов) и GET /manual/{name} (текст markdown).
export interface ManualEntry {
  name: string;
  title: string;
}

export type FetchText = (input: string) => Promise<Response>;

let fetchImpl: FetchText = (input) => fetch(input);

export function setManualFetch(impl: FetchText): void {
  fetchImpl = impl;
}

// Имя документа это путь внутри docs/ или файл корня; каждая часть пути кодируется отдельно.
export const manualUrl = (name: string) => `/manual/${name.split('/').map(encodeURIComponent).join('/')}`;

let indexCache: Promise<ManualEntry[]> | null = null;
const docCache = new Map<string, Promise<string>>();

async function get(url: string): Promise<Response> {
  const response = await fetchImpl(url);
  if (!response.ok) throw await parseError(response);
  return response;
}

export function manualIndex(): Promise<ManualEntry[]> {
  if (!indexCache) {
    indexCache = get('/manual').then((r) => r.json() as Promise<ManualEntry[]>);
    indexCache.catch(() => (indexCache = null));
  }
  return indexCache;
}

export function manualDoc(name: string): Promise<string> {
  let cached = docCache.get(name);
  if (!cached) {
    cached = get(manualUrl(name)).then((r) => r.text());
    docCache.set(name, cached);
    cached.catch(() => docCache.delete(name));
  }
  return cached;
}

export function clearManualCache(): void {
  indexCache = null;
  docCache.clear();
}
