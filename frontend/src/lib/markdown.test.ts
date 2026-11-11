import { readdirSync, readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { createSlugger, linkDecisionCodes, renderMarkdown, resolveDocLink, resolveDocMention, slugify } from './markdown';

const ROOT = new URL('../../../', import.meta.url);
const DOC_NAMES = readdirSync(new URL('docs/', ROOT)).filter((f) => f.endsWith('.md'));
const read = (name: string) => readFileSync(new URL(['README.md', 'CONTRIBUTING.md'].includes(name) ? name : `docs/${name}`, ROOT), 'utf8');
const ALL = ['README.md', 'CONTRIBUTING.md', ...DOC_NAMES];

describe('slugify', () => {
  it('даёт идентификаторы как на GitHub: кириллица остаётся, пробелы становятся дефисами', () => {
    expect(slugify('Линии риска')).toBe('линии-риска');
    expect(slugify('Сверка прогноза из прошлого с фактом')).toBe('сверка-прогноза-из-прошлого-с-фактом');
    expect(slugify('Почему не Штёрмер: точка отсчёта широты обрезания (D62)')).toBe('почему-не-штёрмер-точка-отсчёта-широты-обрезания-d62');
    expect(slugify('Слой 1: из стандартов и литературы')).toBe('слой-1-из-стандартов-и-литературы');
    expect(slugify('kp_suppression_deg_per_kp: оценка по рис. 6')).toBe('kp_suppression_deg_per_kp-оценка-по-рис-6');
    expect(slugify('A — b')).toBe('a--b');
  });

  it('повтор заголовка получает суффикс', () => {
    const slug = createSlugger();
    expect([slug('Итог'), slug('Итог'), slug('Итог')]).toEqual(['итог', 'итог-1', 'итог-2']);
  });
});

describe('renderMarkdown', () => {
  it('ставит id заголовкам и собирает оглавление', () => {
    const doc = renderMarkdown('# Модель\n\n## Линии риска\n\n### `GET /manual` и *ещё*\n', 'MODEL.md');
    expect(doc.title).toBe('Модель');
    expect(doc.headings.map((h) => h.id)).toEqual(['модель', 'линии-риска', 'get-manual-и-ещё']);
    expect(doc.html).toContain('<h2 id="линии-риска">Линии риска<a class="md-anchor" href="#/manual/MODEL.md#линии-риска"');
  });

  it('не пропускает сырой HTML, скрипты и чужие схемы ссылок', () => {
    const doc = renderMarkdown('<script>alert(1)</script>\n\nтекст <img src=x onerror=alert(1)> [ссылка](javascript:alert(1)) ![картинка](https://example.org/x.png)\n', 'README.md');
    expect(doc.html).not.toMatch(/<script|<img|javascript:/i);
    expect(doc.html).toContain('&lt;script&gt;alert(1)&lt;/script&gt;');
    expect(doc.html).toContain('картинка');
  });

  it('переписывает ссылки между документами на раздел «Документация»', () => {
    const readme = renderMarkdown('[модель](docs/MODEL.md), [окна](docs/MODEL.md#сравнение-окон), [правила](CONTRIBUTING.md), [озвучка](tts/README.md), [uv](https://docs.astral.sh/uv/)', 'README.md').html;
    expect(readme).toContain('href="#/manual/MODEL.md"');
    expect(readme).toContain('href="#/manual/MODEL.md#сравнение-окон"');
    expect(readme).toContain('href="#/manual/CONTRIBUTING.md"');
    expect(readme).toContain('<span class="md-nolink"');
    expect(readme).not.toContain('href="tts/README.md"');
    expect(readme).toContain('href="https://docs.astral.sh/uv/" target="_blank" rel="noopener noreferrer"');
    const inDocs = renderMarkdown('[база](SCIENTIFIC_BASIS.md), [корень](../README.md), [раздел](#линии-риска)', 'DEPENDENCIES.md').html;
    expect(inDocs).toContain('href="#/manual/SCIENTIFIC_BASIS.md"');
    expect(inDocs).toContain('href="#/manual/README.md"');
    expect(inDocs).toContain('href="#/manual/DEPENDENCIES.md#линии-риска"');
  });

  it('делает ссылкой имя документа в коде, если такой документ есть', () => {
    const html = renderMarkdown('`docs/DECISIONS.md`, `README.md`, `MODEL.md`, `report.md`, `src/evarisk/windows/README.md`', 'MODEL.md').html;
    expect(html).toContain('<a class="md-doc-link" href="#/manual/DECISIONS.md"><code>docs/DECISIONS.md</code></a>');
    expect(html).toContain('<a class="md-doc-link" href="#/manual/README.md"><code>README.md</code></a>');
    expect(html).toContain('<a class="md-doc-link" href="#/manual/MODEL.md"><code>MODEL.md</code></a>');
    expect(html).toContain('<code>report.md</code>');
    expect(html).not.toContain('href="#/manual/report.md"');
    expect(html).toContain('<code>src/evarisk/windows/README.md</code>');
    expect(renderMarkdown('`MODEL.md`', 'API.md', { known: ['API.md'] }).html).not.toContain('<a');
  });

  it('связывает коды решений с журналом, кроме кода, ссылок и заголовков', () => {
    const doc = renderMarkdown('## Правило (D62)\n\nПо D49 и (D70); `D48` в коде; 3D-сцена.\n', 'MODEL.md');
    expect(doc.html).toContain('<a class="md-decision" href="#/manual/DECISIONS.md#d49"');
    expect(doc.html).toContain('href="#/manual/DECISIONS.md#d70"');
    expect(doc.html).not.toContain('DECISIONS.md#d48');
    expect(doc.html).not.toContain('DECISIONS.md#d62');
    expect(doc.html).not.toContain('DECISIONS.md#d3');
    expect(linkDecisionCodes('<a href="x">D12</a> D13')).toBe('<a href="x">D12</a> <a class="md-decision" href="#/manual/DECISIONS.md#d13" title="Решение D13 в журнале решений">D13</a>');
  });

  it('в журнале решений строка с кодом в первой ячейке получает id', () => {
    const table = '| № | Решение |\n|---|---|\n| D12 | uv |\n| D70 | пробел |\n';
    expect(renderMarkdown(table, 'DECISIONS.md').html).toContain('<tr id="d70">');
    expect(renderMarkdown(table, 'DECISIONS.md').ids).toEqual(['d12', 'd70']);
    expect(renderMarkdown(table, 'MODEL.md').html).not.toContain('id="d70"');
  });
});

describe('ссылки документов разрешаются', () => {
  it('относительные пути считаются от каталога документа', () => {
    expect(resolveDocLink('docs/MODEL.md#x', 'README.md')).toEqual({ name: 'MODEL.md', anchor: 'x' });
    expect(resolveDocLink('examples/README.md', 'RESULTS.md')).toEqual({ name: 'examples/README.md', anchor: undefined });
    expect(resolveDocLink('../pyproject.toml', 'MODEL.md')).toBeNull();
    expect(resolveDocMention('docs/examples/README.md')).toEqual({ name: 'examples/README.md', anchor: undefined });
  });

  // Настоящие документы: каждая ссылка на раздел документа ведёт к существующему заголовку или строке журнала.
  it('в настоящих документах все ссылки на документы и разделы существуют', () => {
    const rendered = new Map(ALL.map((name) => [name, renderMarkdown(read(name), name, { known: ALL })]));
    const broken: string[] = [];
    for (const [name, doc] of rendered) {
      for (const m of doc.html.matchAll(/href="#\/manual\/([^"#]+)(?:#([^"]*))?"/g)) {
        const target = m[1];
        const anchor = m[2] ? m[2].replace(/&amp;/g, '&') : '';
        const other = rendered.get(target);
        if (!other) {
          if (!/\//.test(target)) broken.push(`${name} → ${target}`);
          continue;
        }
        if (anchor && !other.ids.includes(anchor)) broken.push(`${name} → ${target}#${anchor}`);
      }
    }
    expect(broken).toEqual([]);
  });
});
