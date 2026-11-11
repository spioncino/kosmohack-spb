import type { Briefing, BriefingQuestion, BriefingWindow } from '../api/client';
import { fmtTime, fmtUtc, minutesLabel } from './format';
import { AVAILABILITY_LABEL, GRADE_LABEL, LEVEL_LABEL, LINE_ORDER, lineLabel } from './labels';

export const QUESTIONS: BriefingQuestion[] = ['summary', 'can_go_now', 'when_better', 'what_blocks'];

export const QUESTION_LABEL: Record<BriefingQuestion, string> = {
  summary: 'Сводка',
  can_go_now: 'Идём по плану?',
  when_better: 'Когда лучше?',
  what_blocks: 'Что мешает?',
};

export const CHIP_MODEL = 'сформулировано моделью · числа проверены';
export const CHIP_TEMPLATE = 'шаблон: модель недоступна';
const CHIP_MODEL_TITLE = 'Текст собран языковой моделью по фактам расчёта; каждое число и время сверены со списком фактов.';
const CHIP_TEMPLATE_TITLE = 'Текст собран из фактов расчёта без модели: числа те же, формулировка постоянная.';

export interface SourceChip {
  text: string;
  tone: 'model' | 'template';
  title: string;
}

// Чип говорит, откуда взялась формулировка. Модель только формулирует: числа в обоих случаях из расчёта.
export function sourceChip(briefing: Briefing): SourceChip {
  if (briefing.source === 'llm') {
    const model = briefing.model ? ` (${briefing.model})` : '';
    return { text: CHIP_MODEL, tone: 'model', title: `${CHIP_MODEL_TITLE}${model}` };
  }
  return { text: CHIP_TEMPLATE, tone: 'template', title: CHIP_TEMPLATE_TITLE };
}

// Причина шаблонного текста показывается, только когда модель отвечала и её текст не прошёл проверку.
export function fallbackReason(briefing: Briefing): string | null {
  if (briefing.source !== 'template' || !briefing.violations.length) return null;
  return `Текст модели не принят: ${briefing.violations.join('; ')}`;
}

// Подпись карточки называет основание данных: прогноз из прошлого и разбор не выдаются за обстановку сейчас.
export function subtitle(briefing: Briefing): string {
  const facts = briefing.facts;
  if (facts.mode === 'replay' && facts.cutoff) return `по данным на ${fmtUtc(facts.cutoff)}`;
  if (facts.mode === 'review') return 'разбор по архиву, не прогноз';
  return facts.data_moment ? `данные на ${fmtUtc(facts.data_moment)}` : 'данные на момент запроса';
}

export type SpeechMode = 'off' | 'audio' | 'browser' | 'unsupported';

// Звук: файл сервера, если он есть; иначе речь браузера по speech_text; без поддержки браузером сказано прямо.
export function speechMode(briefing: Briefing | null, voiceOn: boolean, browserSpeech: boolean): SpeechMode {
  if (!voiceOn || !briefing) return 'off';
  if (briefing.audio_url) return 'audio';
  return browserSpeech ? 'browser' : 'unsupported';
}

export interface FactRow {
  label: string;
  value: string;
  rawIds: string[];
}

function windowText(start: string, end: string): string {
  return `${fmtUtc(start)} – ${fmtTime(end)}`;
}

// Названия линий как в тексте сводки (src/evarisk/briefing/templates.py: LINE_NAMES).
const LINE_NAME: Record<string, string> = {
  sep_protons: 'протоны',
  meteor_showers: 'метеорные потоки',
  conjunctions: 'сближения',
  dose_proxy: 'прокси дозы',
  illumination: 'освещённость',
  proton_access_geometry: 'геометрия доступа протонов',
};

// Минуты окна по каждой линии штрафа (D84): «высокий и критический уровень: протоны 74 мин, метеорные потоки 0 мин».
// Линии идут одновременно, поэтому минуты не складываются: сумма бывает больше длины окна. У сводок без полей
// *_by_line (кеш до D84) показывается прежняя сумма: «высокий и критический уровень 40 мин».
function levelMinutes(label: string, byLine: Record<string, number> | undefined, total: number | null): string {
  const names = byLine ? Object.keys(byLine) : [];
  if (!byLine || !names.length) return `${label} ${minutesLabel(total)}`;
  const ordered = [...LINE_ORDER.filter((n) => n in byLine), ...names.filter((n) => !LINE_ORDER.includes(n as never))];
  return `${label}: ${ordered.map((line) => `${LINE_NAME[line] || lineLabel(line)} ${minutesLabel(byLine[line])}`).join(', ')}`;
}

function windowMinutes(window: BriefingWindow, withUnknown: boolean): string {
  const exposure = levelMinutes('высокий и критический уровень', window.exposure_by_line, window.exposure_minutes);
  return withUnknown ? `${exposure} · ${levelMinutes('без уровня', window.unknown_by_line, window.unknown_minutes)}` : exposure;
}

// Факты карточки: то же, из чего собран текст. Числа берутся из полей сводки, текст не разбирается.
export function factRows(briefing: Briefing): FactRow[] {
  const facts = briefing.facts;
  const rawIds = facts.provenance.map((p) => p.raw_id);
  const rows: FactRow[] = [
    { label: 'Основание данных', value: facts.basis, rawIds: [] },
    {
      label: 'Запрошенное окно',
      value: `${windowText(facts.requested.start, facts.requested.end)} · ${windowMinutes(facts.requested, true)}`,
      rawIds,
    },
  ];
  if (facts.recommended) {
    rows.push({
      label: 'Рекомендованное окно',
      value: `${windowText(facts.recommended.start, facts.recommended.end)} · ${windowMinutes(facts.recommended, false)}`,
      rawIds,
    });
  }
  for (const item of facts.exposed) {
    rows.push({
      label: `Отрезок ${LEVEL_LABEL[item.level]}`,
      value: `${windowText(item.start, item.end)} · ${lineLabel(item.line)} · ${minutesLabel(item.minutes)}`,
      rawIds,
    });
  }
  if (facts.exposed_total > facts.exposed.length) {
    rows.push({ label: 'Отрезков всего', value: String(facts.exposed_total), rawIds: [] });
  }
  if (facts.clear_after) {
    rows.push({ label: 'После этого момента таких минут нет', value: fmtUtc(facts.clear_after), rawIds: [] });
  }
  for (const gap of facts.gaps) {
    rows.push({ label: 'Пробел данных', value: `${lineLabel(gap.line)}: ${AVAILABILITY_LABEL[gap.availability]}`, rawIds: [] });
  }
  if (facts.disabled_sources.length) {
    rows.push({ label: 'Источники отключены', value: facts.disabled_sources.join(', '), rawIds: [] });
  }
  if (facts.confidence_grade) {
    rows.push({
      label: 'Уверенность линии протонов',
      value: `${GRADE_LABEL[facts.confidence_grade]}${facts.confidence_depends_on ? ` · зависит от: ${facts.confidence_depends_on}` : ''}`,
      rawIds: [],
    });
  }
  if (facts.marginal_note) rows.push({ label: 'Пограничные витки', value: facts.marginal_note, rawIds: [] });
  rows.push({ label: 'Окна', value: `исключено ${facts.windows_excluded} из ${facts.windows_total}`, rawIds: [] });
  return rows;
}

// Контекст следующего своего вопроса (D89): до трёх последних отвеченных моделью пар, от старой к новой. Ответы по
// шаблону в контекст не идут: вопроса они не отвечали.
export const ASK_TURNS = 3;
export interface AskTurn {
  asked: string;
  answer: string;
}
export function askTurns(history: { asked: string; text: string; source: Briefing['source'] }[]): AskTurn[] {
  return history
    .filter((item) => item.source === 'llm')
    .slice(0, ASK_TURNS)
    .reverse()
    .map((item) => ({ asked: item.asked, answer: item.text }));
}
