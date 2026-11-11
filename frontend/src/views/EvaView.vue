<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import type { AssessmentRequest, BriefingQuestion, Interval, Level, Mode } from '../api/client';
import { exportUrl, jsonUrl } from '../api/client';
import { QUESTIONS, QUESTION_LABEL, factRows, fallbackReason, sourceChip, speechMode, subtitle } from '../lib/briefing';
import Badges from '../components/Badges.vue';
import Icon from '../components/Icon.vue';
import LineMinutes from '../components/LineMinutes.vue';
import PlainList from '../components/PlainList.vue';
import PlainParts from '../components/PlainParts.vue';
import RecTechnical from '../components/RecTechnical.vue';
import Segments from '../components/Segments.vue';
import Term from '../components/Term.vue';
import PlanChangeBanner from '../components/PlanChangeBanner.vue';
import StaleBanner from '../components/StaleBanner.vue';
import ProgressSteps from '../components/ProgressSteps.vue';
import ProvenanceList from '../components/ProvenanceList.vue';
import Timeline from '../components/Timeline.vue';
import VoiceAskButton from '../components/VoiceAskButton.vue';
import { fmtTime, fmtUtc, fromLocalInput, minutesLabel, ms, num, percent, toLocalInput } from '../lib/format';
import { REFRESH_EVERY_MS, REFRESH_MIN_GAP_MS } from '../lib/refresh';
import { MAX_START_HOURS, periodCaption, searchPeriodFrom, startPeriodError } from '../lib/period';
import { formDifferences, formFromRequest, isOpenedStart, staleNotice, startHoursStep } from '../lib/requestForm';
import { currentRangeIssue } from '../lib/currentMode';
import { KIND_LABEL, MODE_LABEL, lineLabel } from '../lib/labels';
import { hoursByLine, maxHours, unmeasuredNote } from '../lib/score';
import { COMPARISON_CAPTION, comparisonRows, requestedExposureText } from '../lib/baselines';
import { equivalentsExplanation, verificationVerdict } from '../lib/verdict';
import { parseReason, perLineRows } from '../lib/reason';
import { HOW_CHOSEN, minutesOf, plainExclusions, plainExplanation } from '../lib/plain';
import { addToArchive, askOwnQuestion, askedHistory, assessment, assessmentError, assessmentLoading, autoRefreshState, autoRefreshedId, briefing, clock, briefingError, briefingLoading, briefingQuestion, job, jobError, jobRunning, loadBriefing, loadOrbit, notify, openAssessment, openEvidence, orbit, runAssessment, scores, selectedTime, setSelectedTime, setVoice, userJobRunning, voiceOn } from '../store/state';

const router = useRouter();
const route = useRoute();

// Ссылка вида #/eva?id=<assessment_id> открывает конкретный расчёт сервера.
onMounted(() => {
  const id = typeof route.query.id === 'string' ? route.query.id : '';
  if (id && assessment.value?.id !== id) openAssessment(id);
  // Конец покрытия последней эфемериды для проверки дат режима «сейчас» (D87).
  loadOrbit();
});

const form = reactive({
  mode: 'current' as Mode,
  cutoff: '',
  evaStart: toLocalInput(selectedTime.value),
  duration: 6,
  // Период допустимого начала ВКД: его начало и длина в часах; период поиска для API из них выводится.
  startFrom: toLocalInput(selectedTime.value.getTime() - 4 * 3600000),
  startHours: 6,
  minSunlit: '',
  startInDaylight: false,
});
const formError = ref('');
const modeHint = ref('');

// Выбранный момент ставится в середину периода допустимого начала, но не дальше 4 ч от его начала.
function shiftStartPeriod(evaStartIso: string, startHours: number): void {
  const back = Math.min(4 * 3600000, Math.max(0, startHours * 3600000) / 2);
  form.startFrom = toLocalInput(ms(evaStartIso) - back);
}

// Выбор времени в хедере или на карте двигает начало ВКД и период допустимого начала. Открытие расчёта ставит
// выбранное время на его начало: этот сдвиг форму не трогает, иначе период допустимого начала разошёлся бы с расчётом.
watch(selectedTime, (value) => {
  const opened = assessment.value;
  if (opened && isOpenedStart(value, opened.request, opened.requested_window?.window?.start)) return;
  form.evaStart = toLocalInput(value);
  shiftStartPeriod(value.toISOString(), form.startHours);
});

// Форма отражает запрос открытого расчёта целиком (lib/requestForm.ts), чтобы повторный запуск менял только нужный
// параметр. Результат автообновления правки формы не перезаписывает, но при открытии экрана форма заполняется всегда:
// иначе после автообновления она осталась бы с полями по умолчанию и не совпала бы с показанным расчётом.
let formFilled = false;
watch(assessment, (value) => {
  if (!value || (formFilled && value.id === autoRefreshedId.value)) return;
  fillForm(value.request);
}, { immediate: true });

function fillForm(request: AssessmentRequest): void {
  Object.assign(form, formFromRequest(request));
  formFilled = true;
  modeHint.value = '';
}

// Периоды поиска примеров 12:00–22:00, 00:00–24:00 и ближайшие 12 ч при длительности 6 ч: допустимое начало
// 4, 18 и 6 ч. На этих запросах посчитаны числа в документации.
const presets = [
  { name: 'Разбор по архиву: 10 мая 2024', mode: 'review' as Mode, evaStart: '2024-05-10T14:00', duration: 6, startFrom: '2024-05-10T12:00', startHours: 4, cutoff: '' },
  { name: 'Прогноз из прошлого: 11 мая 2024, отсечение 10 мая 12:30', mode: 'replay' as Mode, evaStart: '2024-05-11T04:00', duration: 6, startFrom: '2024-05-11T00:00', startHours: 18, cutoff: '2024-05-10T12:30' },
  { name: 'Сейчас: ближайшие 12 часов', mode: 'current' as Mode, evaStart: toLocalInput(Math.ceil(Date.now() / 3600000) * 3600000 + 2 * 3600000), duration: 6, startFrom: toLocalInput(Math.ceil(Date.now() / 3600000) * 3600000), startHours: 6, cutoff: '' },
];

// Режим «сейчас» имеет смысл для окна в ближайшие дни: расчёт идёт по данным на момент запроса, а отчёт
// SOCRATES покрывает только текущую неделю. Окно в прошлом при выборе этого режима переносится на ближайшие часы.
function onModeChange(): void {
  modeHint.value = '';
  if (form.mode !== 'current') return;
  const evaStart = fromLocalInput(form.evaStart);
  if (evaStart && ms(evaStart) < Date.now() - 24 * 3600000) {
    const preset = presets.find((p) => p.mode === 'current');
    if (preset) applyPreset(preset);
    modeHint.value = 'Окно было в прошлом и перенесено на ближайшие часы: режим «сейчас» рассчитывает будущее окно по данным на момент запроса. Для прошлого окна подходит «Разбор».';
  }
}

// Режим «сейчас» рассчитывает только ближайшие дни (lib/currentMode.ts, D87): с датами раньше суток до текущего
// момента или дальше покрытия последней эфемериды запрос не отправляется, под началом ВКД объяснение.
const rangeIssue = computed(() => {
  const startFrom = fromLocalInput(form.startFrom);
  const startHours = Number(form.startHours);
  const duration = Number(form.duration);
  const search = startFrom && Number.isFinite(startHours) && Number.isFinite(duration) ? searchPeriodFrom(startFrom, startHours, duration) : null;
  return currentRangeIssue(
    { mode: form.mode, evaStart: fromLocalInput(form.evaStart), searchStart: search?.search_start ?? startFrom, searchEnd: search?.search_end ?? null },
    clock.value,
    orbit.value?.oem?.stop_time,
  );
});

// Прошлые даты режима «сейчас» переходят в разбор по архиву с теми же датами.
function switchToReview(): void {
  form.mode = 'review';
  onModeChange();
}

function applyPreset(p: (typeof presets)[number]): void {
  form.mode = p.mode;
  form.evaStart = p.evaStart;
  form.duration = p.duration;
  form.startFrom = p.startFrom;
  form.startHours = p.startHours;
  form.cutoff = p.cutoff;
}

// Подпись под полями периода: где могут начинаться окна и какой отрезок они покрывают при текущей длительности.
const formPeriod = computed(() => {
  const startFrom = fromLocalInput(form.startFrom);
  const startHours = Number(form.startHours);
  const duration = Number(form.duration);
  if (!startFrom || !Number.isFinite(startHours) || !Number.isFinite(duration)) return '';
  return periodCaption({ ...searchPeriodFrom(startFrom, startHours, duration), duration_hours: duration });
});

function buildRequest(): AssessmentRequest | null {
  const evaStart = fromLocalInput(form.evaStart);
  const startFrom = fromLocalInput(form.startFrom);
  const cutoff = fromLocalInput(form.cutoff);
  if (!evaStart || !startFrom) {
    formError.value = 'Заполните дату и время начала ВКД и периода допустимого начала';
    return null;
  }
  if (form.mode === 'replay' && !cutoff) {
    formError.value = 'Режим «прогноз из прошлого» требует момент отсечения';
    return null;
  }
  const duration = Number(form.duration);
  if (!(duration >= 1 && duration <= 8)) {
    formError.value = 'Длительность ВКД от 1 до 8 часов';
    return null;
  }
  const startHours = Number(form.startHours);
  if (!Number.isFinite(startHours)) {
    formError.value = 'Длина периода допустимого начала задаётся числом часов';
    return null;
  }
  // Период поиска для API: от начала периода допустимого начала до его конца плюс длительность ВКД.
  const search = searchPeriodFrom(startFrom, startHours, duration);
  // То же правило, что в API: отрезок начал от 15 минут до 24 часов, начало ВКД внутри него.
  const periodError = startPeriodError({ ...search, duration_hours: duration, eva_start: evaStart });
  if (periodError) {
    formError.value = periodError;
    return null;
  }
  const minSunlit = form.minSunlit.trim() === '' ? null : Number(form.minSunlit.replace(',', '.'));
  if (minSunlit !== null && !(minSunlit >= 0 && minSunlit <= 1)) {
    formError.value = 'Минимальная доля света задаётся числом от 0 до 1';
    return null;
  }
  formError.value = '';
  const request: AssessmentRequest = {
    mode: form.mode,
    cutoff: form.mode === 'replay' ? cutoff : null,
    eva_start: evaStart,
    duration_hours: duration,
    ...search,
  };
  if (minSunlit !== null || form.startInDaylight) request.constraints = { min_sunlit_fraction: minSunlit, start_in_daylight: form.startInDaylight };
  return request;
}

async function submit(): Promise<void> {
  if (rangeIssue.value) return;
  const request = buildRequest();
  if (!request) return;
  const result = await runAssessment(request);
  if (result) {
    setSelectedTime(result.request.eva_start);
    notify(`Расчёт ${result.id} готов`);
  }
}

const result = computed(() => assessment.value);

// Показанный результат не отвечает форме или идёт новый расчёт пользователя (D85): баннер над результатом и
// приглушённые рекомендация, сводка и карточки окон. Автообновление пересчитывает тот же запрос и баннера не ставит.
const formChanges = computed(() => (result.value ? formDifferences(form, result.value.request) : []));
const stale = computed(() => (result.value ? staleNotice(formChanges.value, userJobRunning.value) : null));

function revertForm(): void {
  if (result.value) fillForm(result.value.request);
}

// Расчёт «сейчас» обновляется сам (store/state.ts: autoRefreshTick): видно время расчёта и срок следующего пересчёта.
// Ручной пересчёт ставит тот же запрос сразу.
const refreshInfo = computed(() => {
  const r = result.value;
  if (!r || r.request.mode !== 'current') return null;
  const plan = autoRefreshState(clock.value);
  return {
    updated: `${fmtTime(r.created_at)} UTC`,
    next: plan.next !== null ? `${fmtTime(Math.max(plan.next, clock.value))} UTC` : null,
    reason: plan.reason === 'new_data' ? 'источники получили новые данные' : null,
  };
});
const REFRESH_RULE = `Тот же запрос пересчитывается сам, пока расчёт открыт и вкладка видна: после новых данных источников не чаще раза в ${REFRESH_MIN_GAP_MS / 60000} мин и без них не реже раза в ${REFRESH_EVERY_MS / 60000} мин. Разбор и прогноз из прошлого не пересчитываются.`;

async function recomputeNow(): Promise<void> {
  const r = result.value;
  if (!r || jobRunning.value) return;
  const fresh = await runAssessment(r.request);
  if (fresh) notify(`Расчёт ${fresh.id} готов`);
}

const requested = computed(() => result.value?.requested_window || null);
// Доля периода поиска без измеренного и продлённого потока протонов (метрика линии unmeasured_minutes, D64).
const unmeasuredShare = computed(() => {
  const sep = result.value?.lines.find((l) => l.line === 'sep_protons');
  const minutes = sep?.metrics?.unmeasured_minutes;
  if (!result.value || sep?.availability !== 'available' || minutes === undefined || minutes === null) return null;
  const total = (Date.parse(result.value.request.search_end) - Date.parse(result.value.request.search_start)) / 60000;
  return total > 0 ? minutes / total : null;
});
// Полнота 100 % при неизмеренном потоке это покрытие уровнями из прогноза, а не измерениями: фраза рядом с полнотой.
const completenessNote = computed(() => unmeasuredNote(scores.value));
const illumination = computed(() => result.value?.lines.find((l) => l.line === 'illumination') || null);
const selectedInterval = ref<{ interval: Interval; line: string } | null>(null);

// Подпись картины называет режим и основание данных, чтобы прогноз до отсечения и реконструкция не путались.
const pictureBasis = computed(() => {
  const r = result.value;
  if (!r) return '';
  if (r.request.mode === 'replay' && r.request.cutoff) return `прогноз из прошлого: только данные, опубликованные до отсечения ${fmtUtc(r.request.cutoff)}`;
  if (r.request.mode === 'review') return 'разбор по архиву: реконструкция по всем опубликованным данным, не прогноз';
  return `сейчас: данные на момент ${fmtUtc(r.asof ?? r.created_at)}`;
});

// Минуты уровней запрошенного окна по линиям: итог это максимум по линиям, а не сумма, потому что одна
// и та же минута окна считается в каждой линии отдельно и сумма превышает длину окна.
const MINUTE_GROUPS: { label: string; levels: Level[]; term: string }[] = [
  { label: 'Опасные минуты (высокий и критический уровень)', levels: ['high', 'critical'], term: 'danger_minutes' },
  { label: 'Повышенный фон (опасная часть до экипажа не доходит)', levels: ['elevated'], term: 'elevated_background' },
];
const minuteGroups = computed(() => {
  const perLine = requested.value?.per_line;
  if (!perLine) return [];
  return MINUTE_GROUPS.map((group) => ({
    label: group.label,
    term: group.term,
    max: minutesOf(maxHours(perLine, group.levels)),
    items: hoursByLine(perLine, group.levels).map((item) => ({ line: item.line, label: item.label, minutes: minutesOf(item.hours) })),
  }));
});
// Минуты по линиям в запрошенном окне обычными словами: «протоны: опасные N мин, повышенный фон M мин, без данных K мин».
const requestedRows = computed(() => perLineRows(requested.value?.per_line));
const requestedExclusions = computed(() => plainExclusions(requested.value?.excluded_reason));
// Объяснение рекомендации обычными словами; у расчётов без поля explanation показывается техническая запись.
const plain = computed(() => (result.value ? plainExplanation(result.value.recommendation) : null));
const noChoice = computed(() => !!result.value && (result.value.recommendation.insufficient || !result.value.recommendation.preferred));
// Предупреждение о неизмеренном потоке (D64) не повторяется, если его уже называет видимый текст: объяснение или
// первая фраза технической причины, когда объяснения нет.
const caveatShown = computed(() => {
  if (!result.value) return false;
  return plain.value ? plain.value.caveat : parseReason(result.value.recommendation.reason).caveat;
});
const equivalentsNote = computed(() => (result.value ? equivalentsExplanation(result.value) : null));
const verdict = computed(() => (result.value?.verification ? verificationVerdict(result.value.verification) : ''));
// Сравнение с простыми подходами в сверке replay; у расчётов без поля baselines таблицы нет.
const comparison = computed(() => (result.value ? comparisonRows(result.value) : []));
const requestedExposure = computed(() => (result.value ? requestedExposureText(result.value) : null));
// Пояснения к месту на странице: показываются в окне пояснения над текстом глоссария (components/Term.vue).
const COMPARE_HINT = {
  exposure: 'Минуты геомагнитного доступа с уровнем ≥ high по факту внутри окна, которое выбрал подход',
  counts: 'Минуты экспозиции за период поиска: прогноз и факт ≥ high; факт без прогноза; прогноз без факта',
  stable: 'Выбранное окно входит в равнозначные лучшему окну по факту',
};

// Подсказки к полям сверки по правилам replay (D41): минута периода поиска относится ровно к одному классу.
const VERIFY_HINT = {
  lead: 'Минуты от отсечения до первого факта ≥ high при условии, что прогноз дал уровень ≥ elevated не позже этого момента; иначе не определена',
  hit: 'Минуты периода поиска, где и прогноз, и факт дали уровень ≥ high',
  miss: 'Минуты, где факт ≥ high, а прогноз ниже или без уровня',
  falseAlarm: 'Минуты, где прогноз ≥ high, а факт ниже',
  quiet: 'Минуты, где и прогноз, и факт ниже high',
  unverified: 'Минуты без данных факта: сверить нечем, спокойными не считаются',
  predictedUnknown: 'Минуты, где прогноз не дал уровня; считаются отдельно от классов выше',
  exposureHit: 'Минуты геомагнитного доступа с уровнем ≥ high и в прогнозе, и по факту: протоны доставали станцию, и прогноз это назвал',
  exposureMiss: 'Минуты, когда протоны доставали станцию по факту, а прогноз их не назвал',
  exposureFalseAlarm: 'Минуты, которые прогноз назвал экспозицией, а по факту её не было',
};

// Сводка для экипажа: короткий текст по открытому расчёту. Числа в нём проверены сервером по фактам расчёта.
const browserSpeech = typeof window !== 'undefined' && 'speechSynthesis' in window;
const speaking = ref(false);
const audioEl = ref<HTMLAudioElement | null>(null);

function stopSpeech(): void {
  speaking.value = false;
  if (browserSpeech) window.speechSynthesis.cancel();
  audioEl.value?.pause();
}

watch(result, (value) => {
  stopSpeech();
  if (value) loadBriefing(value.id, briefingQuestion.value);
}, { immediate: true });

const briefingChip = computed(() => (briefing.value ? sourceChip(briefing.value) : null));
const briefingNote = computed(() => (briefing.value ? fallbackReason(briefing.value) : null));
const briefingSubtitle = computed(() => (briefing.value ? subtitle(briefing.value) : ''));
const briefingFacts = computed(() => (briefing.value ? factRows(briefing.value) : []));
const speechKind = computed(() => speechMode(briefing.value, voiceOn.value, browserSpeech));
const voiceNote = computed(() => {
  const current = briefing.value;
  if (!voiceOn.value || !current) return '';
  if (speechKind.value === 'unsupported') return 'Браузер не умеет читать вслух: текст читается с экрана.';
  if (speechKind.value === 'browser' && current.voice_error) return `Читает браузер. ${current.voice_error}`;
  return '';
});

function askBriefing(question: BriefingQuestion): void {
  stopSpeech();
  if (result.value) loadBriefing(result.value.id, question);
}

const ownQuestion = ref('');
const OWN_QUESTION_MAX = 300;
function askOwn(): void {
  const text = ownQuestion.value.trim();
  if (!text || !result.value || briefingLoading.value) return;
  stopSpeech();
  askOwnQuestion(result.value.id, text);
  ownQuestion.value = '';
}
watch(
  () => result.value?.id,
  () => (askedHistory.value = []),
);

function toggleVoice(event: Event): void {
  const on = (event.target as HTMLInputElement).checked;
  stopSpeech();
  setVoice(on);
  if (on && result.value) loadBriefing(result.value.id, briefingQuestion.value);
}

function speak(): void {
  const current = briefing.value;
  if (!current) return;
  if (speaking.value) {
    stopSpeech();
    return;
  }
  if (speechKind.value === 'audio') {
    audioEl.value?.play();
    speaking.value = true;
    return;
  }
  if (speechKind.value !== 'browser') return;
  const utterance = new SpeechSynthesisUtterance(current.speech_text);
  utterance.lang = 'ru-RU';
  utterance.onend = () => (speaking.value = false);
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(utterance);
  speaking.value = true;
}

onUnmounted(stopSpeech);

function selectInterval(interval: Interval, line: string): void {
  selectedInterval.value = { interval, line };
}

function save(): void {
  if (!result.value) return;
  notify(addToArchive(result.value) ? 'Ссылка на расчёт сохранена в архив этого браузера' : 'Хранилище браузера недоступно');
}

function toOrbit(iso: string): void {
  setSelectedTime(iso);
  router.push('/orbit');
}
</script>
<template>
  <div class="section-toolbar">
    <div class="tab-links">
      <RouterLink to="/eva" class="selected">Расчёт</RouterLink>
      <RouterLink to="/compare">Сравнение окон <span v-if="result">{{ result.windows.length }}</span></RouterLink>
    </div>
    <div class="toolbar-actions">
      <button class="button" type="button" :disabled="!result" @click="save"><Icon name="Save" :size="16" />В архив браузера</button>
      <a v-if="result" class="button" :href="exportUrl(result.id)"><Icon name="Download" :size="16" />Выгрузка zip</a>
    </div>
  </div>

  <form class="panel planning-form" @submit.prevent="submit">
    <div class="planning-presets">
      <span class="eyebrow">ПРИМЕРЫ ЗАПРОСОВ</span>
      <button v-for="p in presets" :key="p.name" type="button" class="text-btn" @click="applyPreset(p)">{{ p.name }}</button>
    </div>
    <div class="planning-grid">
      <label>Режим
        <select v-model="form.mode" @change="onModeChange">
          <option value="current">Сейчас: по всем данным на момент запроса</option>
          <option value="review">Разбор по архиву: реконструкция, не прогноз</option>
          <option value="replay">Прогноз из прошлого: только данные до отсечения</option>
        </select>
        <small v-if="modeHint" class="mode-hint">{{ modeHint }}</small>
      </label>
      <label :class="{ disabled: form.mode !== 'replay' }"><span><Term term="cutoff">Отсечение</Term> (UTC)</span>
        <input type="datetime-local" v-model="form.cutoff" :disabled="form.mode !== 'replay'" />
      </label>
      <label>Начало ВКД (UTC)
        <input type="datetime-local" v-model="form.evaStart" required />
        <div v-if="rangeIssue" class="range-issue" role="alert">
          <span><Icon name="TriangleAlert" :size="15" />{{ rangeIssue.text }}</span>
          <small>{{ rangeIssue.detail }}</small>
          <button v-if="rangeIssue.kind === 'past'" type="button" class="button" @click="switchToReview">Переключить на разбор по архиву</button>
        </div>
      </label>
      <label>Длительность ВКД
        <select v-model.number="form.duration">
          <option v-for="n in [1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5, 5.5, 6, 6.5, 7, 7.5, 8]" :key="n" :value="n">{{ n }} ч</option>
        </select>
      </label>
      <label><span><Term term="start_period">Период допустимого начала</Term> (UTC)</span>
        <input type="datetime-local" v-model="form.startFrom" required />
      </label>
      <label>Длина периода допустимого начала, ч
        <input type="number" v-model.number="form.startHours" min="0.25" :max="MAX_START_HOURS" :step="startHoursStep(form.startHours)" required />
        <small v-if="formPeriod" class="field-hint">{{ formPeriod }}</small>
      </label>
      <label>Минимальная доля света в окне
        <input type="text" inputmode="decimal" v-model="form.minSunlit" placeholder="не задано (0–1)" />
      </label>
      <label class="check">
        <input type="checkbox" v-model="form.startInDaylight" />
        <span>Старт на свету</span>
      </label>
    </div>
    <div class="planning-footer">
      <p v-if="formError" class="form-error"><Icon name="TriangleAlert" :size="16" />{{ formError }}</p>
      <button class="button primary" type="submit" :disabled="jobRunning || !!rangeIssue" :title="rangeIssue ? rangeIssue.text : undefined"><Icon :name="jobRunning ? 'LoaderCircle' : 'Play'" :size="17" :class="{ spinning: jobRunning }" />{{ jobRunning ? 'Расчёт идёт…' : 'Рассчитать' }}</button>
    </div>
  </form>

  <ProgressSteps :job="job" :error="jobError" />
  <p v-if="assessmentError" class="form-error panel inline-error"><Icon name="TriangleAlert" :size="16" />{{ assessmentError }}</p>
  <div v-if="assessmentLoading" class="panel empty-state"><Icon name="LoaderCircle" class="spinning" :size="30" /><p>Загрузка результата…</p></div>

  <template v-if="result">
    <StaleBanner v-if="stale" :notice="stale" :can-revert="formChanges.length > 0" @revert="revertForm" />
    <section v-if="refreshInfo" class="panel refresh-bar" :title="REFRESH_RULE">
      <Icon name="RefreshCw" :size="18" :class="{ spinning: jobRunning }" />
      <span><strong>обновлено {{ refreshInfo.updated }}</strong> · автообновление<template v-if="refreshInfo.next">: следующий пересчёт не позже {{ refreshInfo.next }}<template v-if="refreshInfo.reason">, {{ refreshInfo.reason }}</template></template><small>{{ REFRESH_RULE }}</small></span>
      <button class="button" type="button" :disabled="jobRunning" @click="recomputeNow"><Icon name="RotateCw" :size="16" />Пересчитать сейчас</button>
    </section>
    <section class="panel briefing-card" :class="{ 'stale-dim': stale }">
      <div class="card-header">
        <h2>Сводка для экипажа</h2>
        <span class="muted">{{ briefingSubtitle }}</span>
      </div>
      <div class="briefing-controls">
        <button v-for="q in QUESTIONS" :key="q" type="button" class="briefing-question" :class="{ selected: briefingQuestion === q }" @click="askBriefing(q)">{{ QUESTION_LABEL[q] }}</button>
        <label class="check voice-switch">
          <input type="checkbox" :checked="voiceOn" @change="toggleVoice" />
          <span>Озвучка</span>
        </label>
        <button v-if="voiceOn" class="button" type="button" :disabled="speechKind === 'unsupported' || !briefing" @click="speak"><Icon :name="speaking ? 'X' : 'Play'" :size="16" />{{ speaking ? 'Стоп' : 'Прочитать' }}</button>
      </div>
      <form class="briefing-ask" @submit.prevent="askOwn">
        <input v-model="ownQuestion" type="text" :maxlength="OWN_QUESTION_MAX" placeholder="Свой вопрос по расчёту, например: на каких витках высокий уровень?" aria-label="Свой вопрос по расчёту" />
        <VoiceAskButton :max-length="OWN_QUESTION_MAX" @text="ownQuestion = $event" />
        <button class="button" type="submit" :disabled="!ownQuestion.trim() || briefingLoading"><Icon name="Send" :size="15" />Спросить</button>
      </form>
      <p v-if="briefingLoading" class="muted briefing-status">Сводка готовится…</p>
      <p v-else-if="briefingError" class="form-error briefing-status"><Icon name="TriangleAlert" :size="16" />{{ briefingError }}</p>
      <template v-else-if="briefing">
        <p v-if="briefing.asked" class="briefing-asked"><Icon name="MessageCircleQuestion" :size="15" /><span>{{ briefing.asked }}</span></p>
        <p class="briefing-text">{{ briefing.text }}</p>
        <div class="briefing-meta">
          <span v-if="briefingChip" class="briefing-chip" :class="briefingChip.tone" :title="briefingChip.title">{{ briefingChip.text }}</span>
          <span class="muted">{{ briefing.model && briefing.source === 'llm' ? briefing.model : 'формулировка постоянная' }}</span>
        </div>
        <p v-if="briefingNote" class="muted briefing-status">{{ briefingNote }}</p>
        <p v-if="voiceNote" class="muted briefing-status">{{ voiceNote }}</p>
        <audio v-if="speechKind === 'audio' && briefing.audio_url" ref="audioEl" class="briefing-audio" :src="briefing.audio_url" controls preload="none" @ended="speaking = false"></audio>
        <details v-if="askedHistory.length > 1" class="briefing-facts briefing-history">
          <summary>Прошлые вопросы по этому расчёту ({{ askedHistory.length - 1 }})</summary>
          <ul>
            <li v-for="(item, index) in askedHistory.slice(1)" :key="index"><span class="fact-label">{{ item.asked }}</span><span class="fact-value">{{ item.text }}</span></li>
          </ul>
        </details>
        <details class="briefing-facts">
          <summary>Факты, по которым собран текст ({{ briefingFacts.length }})</summary>
          <ul>
            <li v-for="(row, index) in briefingFacts" :key="index">
              <span class="fact-label">{{ row.label }}</span>
              <span class="fact-value">{{ row.value }}</span>
              <span class="fact-links">
                <button v-for="rawId in row.rawIds" :key="rawId" class="evidence-link" type="button" @click="openEvidence(rawId)">{{ rawId.slice(0, 8) }}</button>
              </span>
            </li>
          </ul>
        </details>
      </template>
    </section>

    <PlanChangeBanner />
    <div class="result-grid" :class="{ 'stale-dim': stale }">
      <section class="panel recommendation" :class="{ insufficient: result.recommendation.insufficient }">
        <div class="card-header">
          <h2>Рекомендация</h2>
          <span class="muted"><Term :term="result.request.mode">{{ MODE_LABEL[result.request.mode] }}</Term> · момент данных {{ result.asof ? fmtUtc(result.asof) : 'все доступные сегодня' }}</span>
        </div>
        <div class="recommendation-body">
          <template v-if="plain">
            <strong :class="noChoice ? 'red' : 'teal'"><Icon :name="noChoice ? 'Ban' : 'CheckCircle2'" :size="20" /><span><PlainParts :parts="plain.headline.parts" /></span></strong>
            <PlainList :items="plain.items" />
          </template>
          <strong v-else-if="noChoice" class="red"><Icon name="Ban" :size="20" />Оснований для выбора недостаточно</strong>
          <strong v-else-if="result.recommendation.preferred" class="teal"><Icon name="CheckCircle2" :size="20" />Предпочтительное окно {{ fmtUtc(result.recommendation.preferred.start) }} – {{ fmtUtc(result.recommendation.preferred.end) }}</strong>
          <p v-if="!caveatShown && unmeasuredShare !== null && unmeasuredShare > 0.5" class="rec-caveat"><Icon name="TriangleAlert" :size="16" /><span>Поток протонов не измерен на {{ percent(unmeasuredShare) }} периода поиска: рекомендация опирается на внешний прогноз и сообщения о событиях, это не измеренное спокойствие.</span></p>
          <p v-if="equivalentsNote" class="rec-explain">{{ equivalentsNote }}</p>
          <RecTechnical v-if="!plain" :result="result" @orbit="toOrbit" />
          <p class="how-chosen">{{ HOW_CHOSEN }}</p>
          <details class="tech-details">
            <summary>Технические подробности: правило выбора и коды решений</summary>
            <RecTechnical v-if="plain" :result="result" @orbit="toOrbit" />
            <ul class="rules-list">
              <li v-for="rule in result.rules" :key="rule"><Segments :text="rule" /></li>
            </ul>
          </details>
        </div>
        <div class="recommendation-actions">
          <RouterLink to="/compare" class="button"><Icon name="Columns3" :size="16" />Сравнить окна</RouterLink>
          <RouterLink to="/risks" class="button"><Icon name="ShieldCheck" :size="16" />Анализ рисков</RouterLink>
          <button v-if="result.recommendation.preferred" class="button" type="button" @click="toOrbit(result.recommendation.preferred.start)"><Icon name="Orbit" :size="16" />На орбите</button>
          <a class="text-btn" :href="jsonUrl(result.id)" target="_blank" rel="noopener">JSON результата <Icon name="ArrowUpRight" :size="14" /></a>
        </div>
      </section>

      <section class="panel requested-card" v-if="requested">
        <div class="card-header"><h2>Запрошенное окно</h2><Badges v-if="requested.excluded_reason" level="high" small /></div>
        <dl class="kv">
          <div><dt>Окно</dt><dd>{{ fmtUtc(requested.window.start) }} – {{ fmtUtc(requested.window.end) }}</dd></div>
          <div v-for="group in minuteGroups" :key="group.label">
            <dt><Term :term="group.term">{{ group.label }}</Term></dt>
            <dd>
              <details v-if="group.items.length" class="hours-detail">
                <summary><span class="mono">{{ group.max }} мин</span><small>максимум по линиям · по линиям: {{ group.items.length }}</small></summary>
                <ul class="hours-list">
                  <li v-for="item in group.items" :key="item.line"><span>{{ item.label }}</span><span class="mono">{{ item.minutes }} мин</span></li>
                </ul>
              </details>
              <template v-else><span class="mono">0 мин</span><small>ни по одной линии</small></template>
            </dd>
          </div>
          <div v-if="requestedRows.length" class="nested">
            <dt>По линиям, минуты</dt>
            <dd><LineMinutes :rows="requestedRows" plain /></dd>
          </div>
          <div><dt><Term term="completeness">Полнота данных</Term></dt><dd>{{ percent(requested.completeness) }}<small v-if="completenessNote" class="yellow">{{ completenessNote }}</small></dd></div>
          <div v-if="requested.excluded_reason"><dt><Term term="window_exclusion">Исключено</Term></dt><dd class="red" :title="requested.excluded_reason"><span v-for="(text, i) in requestedExclusions" :key="i" class="plain-reason">{{ text }}</span></dd></div>
          <template v-if="illumination && illumination.availability === 'available'">
            <div><dt><Term term="illumination">Доля света за период</Term></dt><dd>{{ percent(illumination.metrics.sunlit_fraction) }}</dd></div>
            <div><dt>Самая длинная тень</dt><dd>{{ num(illumination.metrics.longest_shadow_minutes, 0) }} мин</dd></div>
            <div><dt>Старт запрошенного окна</dt><dd>{{ illumination.metrics.starts_in_daylight ? 'на свету' : 'в тени' }}</dd></div>
          </template>
          <div v-else><dt><Term term="illumination">Освещённость</Term></dt><dd><Badges :availability="illumination?.availability || 'missing'" small /></dd></div>
          <div><dt>Версия алгоритма</dt><dd class="mono">{{ result.algorithm_version }}</dd></div>
        </dl>
      </section>
    </div>

    <section class="panel timeline-card" :class="{ 'stale-dim': stale }">
      <div class="card-header">
        <h2>Временная картина</h2>
        <span class="muted">{{ pictureBasis }} · {{ periodCaption(result.request) }}</span>
      </div>
      <Timeline :result="result" @select="selectInterval" />
      <div v-if="selectedInterval" class="interval-detail">
        <div class="card-header">
          <h3>{{ lineLabel(selectedInterval.line) }} · <Badges v-if="selectedInterval.line !== 'saa'" :level="selectedInterval.interval.level" small /> <Badges :kind="selectedInterval.interval.kind" small /></h3>
          <button class="icon-btn" type="button" aria-label="Закрыть" @click="selectedInterval = null"><Icon name="X" :size="16" /></button>
        </div>
        <p><strong>{{ fmtUtc(selectedInterval.interval.start) }} – {{ fmtUtc(selectedInterval.interval.end) }}</strong> · {{ KIND_LABEL[selectedInterval.interval.kind] }}</p>
        <p v-if="selectedInterval.interval.note"><Segments :text="selectedInterval.interval.note" /></p>
        <p class="muted rule-text">Правило: <Segments :text="selectedInterval.interval.rule" /></p>
        <ProvenanceList :items="selectedInterval.interval.provenance" compact />
        <button class="text-btn" type="button" @click="toOrbit(selectedInterval.interval.start)">Положение станции в начале интервала <Icon name="ArrowUpRight" :size="14" /></button>
      </div>
    </section>

    <details class="panel verification-card" :class="{ 'stale-dim': stale }" v-if="result.verification">
      <summary class="card-header">
        <h2>Проверка после события</h2>
        <span class="muted">считается отдельно, после рекомендации; факт это тот же расчёт по всем данным, доступным сегодня, включая опубликованные позже отсечения; в рекомендацию не входит · раскрыть</span>
      </summary>
      <p class="muted note-text">Постановка задачи требует сопоставить прогноз из прошлого с последующими наблюдениями. Выше показан прогноз только по данным до отсечения; здесь, отдельно, факт: тот же расчёт с теми же правилами по всем данным, доступным сегодня, включая опубликованные позже отсечения. Это не независимые наблюдения: ошибки самих правил модели такая проверка не покажет.</p>
      <p v-if="verdict" class="verdict"><Icon name="ClipboardCheck" :size="18" /><span>{{ verdict }}</span></p>
      <Timeline :result="result" variant="verification" @select="selectInterval" />
      <dl class="kv columns">
        <div><dt><Term term="lead_time" :hint="VERIFY_HINT.lead">Заблаговременность</Term></dt><dd>{{ minutesLabel(result.verification.lead_time_minutes) }}<small v-if="result.verification.first_observed_alarm"> · первый факт ≥ high {{ fmtUtc(result.verification.first_observed_alarm) }}</small><small v-if="result.verification.first_predicted_warning"> · первое предупреждение прогноза {{ fmtUtc(result.verification.first_predicted_warning) }}</small></dd></div>
        <template v-if="result.verification.exposure_hit_minutes != null">
          <div><dt><Term term="hits" :hint="VERIFY_HINT.exposureHit">Экспозиция: попадания</Term></dt><dd class="teal">{{ num(result.verification.exposure_hit_minutes, 0) }} мин</dd></div>
          <div><dt><Term term="hits" :hint="VERIFY_HINT.exposureMiss">Экспозиция: пропуски</Term></dt><dd :class="{ red: (result.verification.exposure_miss_minutes || 0) > 0 }">{{ num(result.verification.exposure_miss_minutes || 0, 0) }} мин</dd></div>
          <div><dt><Term term="hits" :hint="VERIFY_HINT.exposureFalseAlarm">Экспозиция: ложные тревоги</Term></dt><dd>{{ num(result.verification.exposure_false_alarm_minutes || 0, 0) }} мин</dd></div>
        </template>
        <template v-if="result.verification.exposure_hit_minutes == null">
          <div><dt><Term term="hits" :hint="VERIFY_HINT.hit">Попадания</Term></dt><dd>{{ num(result.verification.hit_minutes, 0) }} мин</dd></div>
          <div><dt><Term term="hits" :hint="VERIFY_HINT.miss">Пропуски</Term></dt><dd :class="{ red: result.verification.miss_minutes > 0 }">{{ num(result.verification.miss_minutes, 0) }} мин</dd></div>
          <div><dt><Term term="hits" :hint="VERIFY_HINT.falseAlarm">Ложные тревоги</Term></dt><dd>{{ num(result.verification.false_alarm_minutes, 0) }} мин</dd></div>
        </template>
        <div><dt><Term term="quiet_minutes" :hint="VERIFY_HINT.quiet">Спокойно у обоих</Term></dt><dd>{{ num(result.verification.quiet_minutes, 0) }} мин</dd></div>
        <div><dt><Term term="unverified_minutes" :hint="VERIFY_HINT.unverified">Без факта</Term></dt><dd>{{ num(result.verification.unverified_minutes, 0) }} мин</dd></div>
        <div><dt><Term term="predicted_unknown_minutes" :hint="VERIFY_HINT.predictedUnknown">Прогноз без уровня</Term></dt><dd>{{ num(result.verification.predicted_unknown_minutes, 0) }} мин</dd></div>
        <div><dt><Term term="window_stable">Выбор окна</Term></dt><dd :class="result.verification.window_choice_stable ? 'teal' : 'yellow'">{{ result.verification.window_choice_stable ? 'совпал с окном по факту' : 'после события лучшее окно оказалось иным' }}</dd></div>
        <div><dt><Term term="reconstruction">Окно по факту</Term></dt><dd>{{ result.verification.observed_preferred ? `${fmtUtc(result.verification.observed_preferred.start)} – ${fmtUtc(result.verification.observed_preferred.end)}` : 'оснований для выбора нет' }}</dd></div>
        <div><dt><Term term="reconstruction">Данные факта</Term></dt><dd><Badges :availability="result.verification.observed_availability" small /></dd></div>
      </dl>
      <p class="muted note-text"><Segments :text="result.verification.note" /></p>
      <section v-if="comparison.length" class="baseline-compare">
        <h3>Сравнение с простыми подходами</h3>
        <p class="muted baseline-caption">{{ COMPARISON_CAPTION }}</p>
        <p v-if="requestedExposure" class="baseline-requested">{{ requestedExposure }}</p>
        <div class="baseline-scroll">
          <table class="baseline-table">
            <thead>
              <tr>
                <th><Term term="baseline">Подход</Term></th>
                <th>Что предсказал</th>
                <th>Выбранное окно (UTC)</th>
                <th class="key"><Term term="exposure" :hint="COMPARE_HINT.exposure">Экспозиция по факту в выбранном окне, мин</Term></th>
                <th><Term term="hits" :hint="COMPARE_HINT.counts">Попадания / пропуски / ложные</Term><small>минуты экспозиции</small></th>
                <th><Term term="lead_time" :hint="VERIFY_HINT.lead">Заблаговременность</Term></th>
                <th><Term term="window_stable" :hint="COMPARE_HINT.stable">Окно устояло</Term></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in comparison" :key="row.key" :class="{ ours: row.ours }">
                <td class="wrap" :title="row.note || undefined"><strong>{{ row.title }}</strong><small>{{ row.rule }}</small></td>
                <td class="wrap">
                  <Badges :level="row.level ?? 'unknown'" small />
                  <small>{{ row.basis }}</small>
                  <details v-if="row.provenance.length" class="baseline-prov">
                    <summary>записи источников: {{ row.provenance.length }}</summary>
                    <ProvenanceList :items="row.provenance" compact />
                  </details>
                </td>
                <td class="mono">{{ row.window }}</td>
                <td class="key mono" :class="{ red: (row.exposureMinutes ?? 0) > 0, teal: row.exposureMinutes === 0, gray: row.exposureMinutes === null }">{{ row.exposure }}</td>
                <td class="mono">{{ row.counts }}</td>
                <td class="mono">{{ row.lead }}</td>
                <td :class="row.stable ? 'teal' : 'yellow'">{{ row.stable ? 'да' : 'нет' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
      <p v-else-if="requestedExposure" class="baseline-requested standalone">{{ requestedExposure }}</p>
    </details>

    <details class="panel limitations-card" :class="{ 'stale-dim': stale }">
      <summary>Ограничения расчёта ({{ result.limitations.length }})</summary>
      <ul><li v-for="l in result.limitations" :key="l"><Segments :text="l" /></li></ul>
    </details>
  </template>

  <div v-else-if="!assessmentLoading && !jobRunning" class="panel empty-state">
    <Icon name="CalendarDays" :size="38" />
    <h2>Открытого расчёта нет</h2>
    <p>Задайте режим, окно и период допустимого начала и нажмите «Рассчитать». Результат сохраняется на сервере и доступен в отчётах.</p>
  </div>
</template>
