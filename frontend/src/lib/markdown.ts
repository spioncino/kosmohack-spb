import { Marked, type Tokens } from 'marked';
import { DECISION_CODE, DECISIONS_DOC, ROOT_DOCS, manualHref } from './manualLinks';

export { DECISION_CODE, DECISIONS_DOC, ROOT_DOCS, decisionRoute, manualHref, manualRoute } from './manualLinks';

export interface DocHeading {
  depth: number;
  text: string;
  id: string;
}

export interface RenderedDoc {
  html: string;
  title: string;
  headings: DocHeading[];
  ids: string[];
}

export interface RenderOptions {
  // Имена документов из GET /manual: имя файла в `коде` становится ссылкой, только если документ есть в списке.
  known?: string[];
}

// Идентификатор заголовка как на GitHub: нижний регистр, кириллица сохраняется, знаки препинания и символы
// убираются, каждый пробел становится дефисом; дефис и подчёркивание остаются.
export function slugify(text: string): string {
  return text
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{M}\p{N}\p{Pc} -]/gu, '')
    .replace(/ /g, '-');
}

// Повтор заголовка получает суффикс -1, -2, как на GitHub.
export function createSlugger(): (text: string) => string {
  const seen = new Map<string, number>();
  return (text: string) => {
    const base = slugify(text);
    let slug = base;
    let count = seen.get(base) ?? 0;
    while (seen.has(slug)) slug = `${base}-${++count}`;
    seen.set(base, count);
    seen.set(slug, 0);
    return slug;
  };
}

export function escapeHtml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function safeDecode(text: string): string {
  try {
    return decodeURIComponent(text);
  } catch {
    return text;
  }
}

function normalizePath(path: string): string | null {
  const out: string[] = [];
  for (const part of path.split('/')) {
    if (!part || part === '.') continue;
    if (part === '..') {
      if (!out.length) return null;
      out.pop();
    } else out.push(part);
  }
  return out.join('/');
}

export interface DocTarget {
  name: string;
  anchor?: string;
}

// Относительная ссылка markdown в документе `current` на документ раздела. Путь считается от каталога документа,
// как на GitHub; README.md и CONTRIBUTING.md без каталога это файлы корня. Файлы вне docs/ и корня (tts/README.md,
// src/…) раздел не отдаёт: для них null.
export function resolveDocLink(href: string, current: string): DocTarget | null {
  const hashAt = href.indexOf('#');
  const path = hashAt >= 0 ? href.slice(0, hashAt) : href;
  const anchor = hashAt >= 0 ? safeDecode(href.slice(hashAt + 1)) || undefined : undefined;
  if (!path) return { name: current, anchor };
  if (/^[a-z][a-z0-9+.-]*:/i.test(path) || path.startsWith('/')) return null;
  const clean = safeDecode(path).replace(/^\.\//, '');
  if (ROOT_DOCS.includes(clean)) return { name: clean, anchor };
  const inDocs = !ROOT_DOCS.includes(current);
  const base = inDocs ? `docs/${current.split('/').slice(0, -1).join('/')}` : '';
  const full = normalizePath(base ? `${base}/${clean}` : clean);
  if (!full || !full.endsWith('.md')) return null;
  if (ROOT_DOCS.includes(full)) return { name: full, anchor };
  if (full.startsWith('docs/')) return { name: full.slice('docs/'.length), anchor };
  return null;
}

// Имя файла в `коде` («`docs/DECISIONS.md`», «`README.md`», «`MODEL.md`»): пути даны от корня проекта.
export function resolveDocMention(text: string, known?: string[]): DocTarget | null {
  const m = /^((?:docs\/)?[\w.-]+(?:\/[\w.-]+)*\.md)(?:#([^\s#]+))?$/.exec(text.trim());
  if (!m) return null;
  const path = m[1];
  let name: string | null = null;
  if (ROOT_DOCS.includes(path)) name = path;
  else if (path.startsWith('docs/')) name = path.slice('docs/'.length);
  else if (/^[A-Z][A-Z0-9_]*\.md$/.test(path)) name = path;
  if (!name) return null;
  if (known && !known.includes(name)) return null;
  return { name, anchor: m[2] };
}

const EXTERNAL = /^(https?:|mailto:)/i;
const SKIP_TAGS = new Set(['a', 'code', 'pre', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']);

// Коды решений D1…D72 в тексте становятся ссылками на строку журнала решений; внутри ссылок, кода и заголовков
// текст не меняется. Разметка приходит из marked с экранированным текстом, поэтому «<» бывает только у тегов.
export function linkDecisionCodes(html: string): string {
  const parts = html.split(/(<[^>]+>)/);
  let skip = 0;
  for (let i = 0; i < parts.length; i++) {
    const part = parts[i];
    if (part.startsWith('<')) {
      const tag = /^<(\/?)([a-z0-9]+)/i.exec(part);
      if (tag && SKIP_TAGS.has(tag[2].toLowerCase())) skip = Math.max(0, skip + (tag[1] ? -1 : 1));
      continue;
    }
    if (skip === 0 && part) {
      parts[i] = part.replace(DECISION_CODE, (code) => `<a class="md-decision" href="${escapeHtml(manualHref(DECISIONS_DOC, code.toLowerCase()))}" title="Решение ${code} в журнале решений">${code}</a>`);
    }
  }
  return parts.join('');
}

// Markdown документа в HTML раздела «Документация». Сырой HTML из файла экранируется и показывается текстом,
// картинки заменяются подписью, ссылки допускаются только http(s), mailto и документы раздела.
export function renderMarkdown(source: string, name: string, options: RenderOptions = {}): RenderedDoc {
  const slug = createSlugger();
  const headings: DocHeading[] = [];
  const ids: string[] = [];
  const decisions = name === DECISIONS_DOC;
  const marked = new Marked({ gfm: true, breaks: false });
  marked.use({
    renderer: {
      html({ text }: Tokens.HTML | Tokens.Tag) {
        return escapeHtml(text);
      },
      image({ text }: Tokens.Image) {
        return escapeHtml(text);
      },
      heading({ tokens, depth }: Tokens.Heading) {
        const plain = this.parser.parseInline(tokens, this.parser.textRenderer);
        const id = slug(plain);
        headings.push({ depth, text: plain, id });
        ids.push(id);
        const anchor = `<a class="md-anchor" href="${escapeHtml(manualHref(name, id))}" aria-label="Ссылка на раздел">#</a>`;
        return `<h${depth} id="${escapeHtml(id)}">${this.parser.parseInline(tokens)}${anchor}</h${depth}>\n`;
      },
      link({ href, title, tokens }: Tokens.Link) {
        const inner = this.parser.parseInline(tokens);
        const titleAttr = title ? ` title="${escapeHtml(title)}"` : '';
        if (EXTERNAL.test(href)) return `<a href="${escapeHtml(href)}"${titleAttr} target="_blank" rel="noopener noreferrer">${inner}</a>`;
        if (/^[a-z][a-z0-9+.-]*:/i.test(href)) return inner;
        const target = resolveDocLink(href, name);
        if (target) return `<a class="md-doc-link" href="${escapeHtml(manualHref(target.name, target.anchor))}"${titleAttr}>${inner}</a>`;
        return `<span class="md-nolink" title="${escapeHtml(`${href}: файл репозитория, в разделе не открывается`)}">${inner}</span>`;
      },
      codespan({ text }: Tokens.Codespan) {
        const code = `<code>${escapeHtml(text)}</code>`;
        const target = resolveDocMention(text, options.known);
        return target ? `<a class="md-doc-link" href="${escapeHtml(manualHref(target.name, target.anchor))}">${code}</a>` : code;
      },
      table(token: Tokens.Table) {
        const cell = (c: Tokens.TableCell, tag: 'th' | 'td') => {
          const align = c.align ? ` style="text-align:${c.align}"` : '';
          return `<${tag}${align}>${this.parser.parseInline(c.tokens)}</${tag}>`;
        };
        const head = `<tr>${token.header.map((c) => cell(c, 'th')).join('')}</tr>`;
        const rows = token.rows
          .map((row) => {
            const first = row[0]?.text.trim() ?? '';
            let id = '';
            if (decisions && /^D\d+$/.test(first)) {
              id = first.toLowerCase();
              ids.push(id);
            }
            return `<tr${id ? ` id="${id}"` : ''}>${row.map((c) => cell(c, 'td')).join('')}</tr>`;
          })
          .join('\n');
        return `<div class="md-table${decisions ? ' md-decisions' : ''}"><table>\n<thead>${head}</thead>\n<tbody>${rows}</tbody>\n</table></div>\n`;
      },
    },
  });
  const html = linkDecisionCodes(marked.parse(source, { async: false }) as string);
  const title = headings.find((h) => h.depth === 1)?.text || name;
  return { html, title, headings, ids };
}
