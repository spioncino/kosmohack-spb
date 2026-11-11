// Адреса раздела «Документация» без зависимости от разборщика markdown: их используют глоссарий и причины
// рекомендации на всех страницах, а сам разборщик грузится только с разделом (lib/markdown.ts).

// Документы корня проекта; остальные имена раздела это пути внутри docs/ (как в GET /manual).
export const ROOT_DOCS = ['README.md', 'CONTRIBUTING.md'];
export const DECISIONS_DOC = 'DECISIONS.md';

// Коды решений журнала: D1…D72.
export const DECISION_CODE = /\bD(\d{1,3})\b/g;

// Путь маршрута для RouterLink и адрес ссылки в разметке: #/manual/<имя>#<якорь>.
export function manualRoute(name: string, anchor?: string): string {
  return `/manual/${name}${anchor ? `#${anchor}` : ''}`;
}

export function manualHref(name: string, anchor?: string): string {
  return `#${manualRoute(name, anchor)}`;
}

export function decisionRoute(code: string): string {
  return manualRoute(DECISIONS_DOC, code.toLowerCase());
}
