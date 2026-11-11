import { computed, ref, shallowRef, watch } from 'vue';
import { detectPlanChange, keepPlanChange, type PlanChange } from '../lib/plan';
import {
  api,
  errorText,
  waitJob,
  type AssessmentListItem,
  type AssessmentRequest,
  type AssessmentResult,
  type Briefing,
  type BriefingQuestion,
  type Evidence,
  type Health,
  type Job,
  type OrbitLatest,
  type Rules,
  type SourceState,
  type Trajectory,
  type TrajectorySample,
} from '../api/client';
import { askTurns } from '../lib/briefing';
import { isoZ, ms } from '../lib/format';
import { interpolatePoint, nearestSample, propagateGeo, satrecFromOmm, sgp4Track, speedFromSamples, type GeoPoint } from '../lib/orbit';
import { collectEvents, lineScores } from '../lib/score';
import { keepsBriefing, latestGuard } from '../lib/latest';
import { newestSuccess, refreshPlan, type RefreshPlan } from '../lib/refresh';

// Часы интерфейса; тикают раз в секунду из App.vue.
export const clock = ref(Date.now());

// Выбранное время расчёта (UTC): задаётся в хедере, ползунком орбиты и переходами из событий.
export const selectedTime = ref(new Date(Math.floor(Date.now() / 60000) * 60000));

export function setSelectedTime(value: Date | number | string): void {
  selectedTime.value = new Date(value);
}

export const health = ref<Health | null>(null);
export const healthError = ref('');
export const sources = ref<SourceState[]>([]);
export const sourcesError = ref('');
export const sourcesLoadedAt = ref<number | null>(null);

export async function loadHealth(): Promise<void> {
  try {
    health.value = await api.health();
    healthError.value = '';
  } catch (error) {
    healthError.value = errorText(error);
  }
}

export async function loadSources(): Promise<void> {
  try {
    sources.value = await api.sources();
    sourcesError.value = '';
    sourcesLoadedAt.value = Date.now();
  } catch (error) {
    sourcesError.value = errorText(error);
  }
}

// Самая старая успешная запись среди активных источников определяет актуальность данных.
export const oldestSuccess = computed(() => {
  const times = sources.value.filter((s) => s.state === 'active').map((s) => s.last_success_at).filter((t): t is string => !!t);
  if (!times.length) return null;
  return times.reduce((a, b) => (ms(a) < ms(b) ? a : b));
});

export const orbit = ref<OrbitLatest | null>(null);
export const orbitError = ref('');
export const orbitUnavailable = ref(false);

export async function loadOrbit(): Promise<void> {
  try {
    orbit.value = await api.orbitLatest();
    orbitError.value = '';
    orbitUnavailable.value = false;
  } catch (error) {
    orbitError.value = errorText(error);
    orbitUnavailable.value = true;
  }
}

export const satrec = computed(() => (orbit.value?.omm ? satrecFromOmm(orbit.value.omm) : null));

export const rules = ref<Rules | null>(null);
export const rulesError = ref('');

export async function loadRules(): Promise<void> {
  try {
    rules.value = await api.rules();
    rulesError.value = '';
  } catch (error) {
    rulesError.value = errorText(error);
  }
}

// Траектория для сцены и телеметрии: из GET /trajectory на [выбранное время − 1.5 ч, + 3 ч].
export const trajectory = shallowRef<Trajectory | null>(null);
export const trajectoryError = ref('');
export const trajectoryLoading = ref(false);
let trajectoryKey = '';

const TRAJ_BACK_MS = 90 * 60000;
const TRAJ_AHEAD_MS = 180 * 60000;

export async function loadTrajectory(force = false): Promise<void> {
  const centre = Math.floor(selectedTime.value.getTime() / (30 * 60000)) * 30 * 60000;
  const key = String(centre);
  if (!force && key === trajectoryKey && (trajectory.value || trajectoryError.value)) return;
  trajectoryKey = key;
  trajectoryLoading.value = true;
  // Для моментов старше суток берётся эфемерида из архива (review), иначе текущая (current).
  const mode = centre < Date.now() - 24 * 3600000 ? 'review' : 'current';
  try {
    trajectory.value = await api.trajectory(isoZ(centre - TRAJ_BACK_MS), isoZ(centre + TRAJ_AHEAD_MS), 1, mode);
    trajectoryError.value = '';
  } catch (error) {
    trajectory.value = null;
    trajectoryError.value = errorText(error);
  } finally {
    trajectoryLoading.value = false;
  }
}

export const assessment = shallowRef<AssessmentResult | null>(null);
export const assessmentError = ref('');
export const assessmentLoading = ref(false);
export const assessmentList = ref<AssessmentListItem[]>([]);
export const assessmentListError = ref('');

// Изменение плана: новый расчёт отличается от прежнего открытого только длительностью ВКД. Хранится сводка
// обоих расчётов; баннер показывается у нового расчёта, открытие постороннего расчёта его снимает.
export const planChange = ref<PlanChange | null>(null);

export function dismissPlanChange(): void {
  planChange.value = null;
}

const LAST_ID_KEY = 'evarisk.lastAssessmentId';

function readLastId(): string | null {
  try {
    return localStorage.getItem(LAST_ID_KEY);
  } catch {
    return null;
  }
}

function writeLastId(id: string): void {
  try {
    localStorage.setItem(LAST_ID_KEY, id);
  } catch {
    // Хранилище браузера недоступно: идентификатор живёт только в памяти.
  }
}

// Открытый расчёт переводит выбранное время на начало запрошенного окна: карта, телеметрия и события
// показывают период расчёта, а не текущий момент.
function focusOn(result: AssessmentResult): void {
  setSelectedTime(result.requested_window.window.start);
}

export async function loadAssessmentList(): Promise<void> {
  try {
    assessmentList.value = await api.assessments();
    assessmentListError.value = '';
  } catch (error) {
    assessmentListError.value = errorText(error);
  }
}

// Ответ на более раннее открытие расчёта, пришедший позже, не подменяет открытый расчёт.
const assessmentRequests = latestGuard();

// focus = false оставляет выбранное время как есть: так открывается результат автообновления.
export async function openAssessment(id: string, focus = true): Promise<AssessmentResult | null> {
  assessmentLoading.value = true;
  const request = assessmentRequests.begin();
  try {
    const result = await api.assessment(id);
    if (!assessmentRequests.isCurrent(request)) return null;
    assessment.value = result;
    planChange.value = keepPlanChange(planChange.value, result.id);
    assessmentError.value = '';
    writeLastId(id);
    if (focus) focusOn(result);
    return result;
  } catch (error) {
    if (assessmentRequests.isCurrent(request)) assessmentError.value = errorText(error);
    return null;
  } finally {
    if (assessmentRequests.isCurrent(request)) assessmentLoading.value = false;
  }
}

// При старте открывается расчёт из этого браузера, иначе самый свежий на сервере; явно запрошенный
// экраном расчёт (например, по ссылке #/eva?id=…) имеет приоритет и не перекрывается.
export async function restoreAssessment(): Promise<void> {
  const explicit = () => assessment.value !== null || assessmentLoading.value;
  if (explicit()) return;
  const last = readLastId();
  if (last) {
    const result = await api.assessment(last).catch(() => null);
    if (explicit()) return;
    if (result) {
      assessment.value = result;
      focusOn(result);
      return;
    }
  }
  await loadAssessmentList();
  if (explicit()) return;
  const newest = assessmentList.value[0];
  if (newest) await openAssessment(newest.id);
}

export const briefing = ref<Briefing | null>(null);
export const briefingError = ref('');
export const briefingLoading = ref(false);
export const briefingQuestion = ref<BriefingQuestion>('summary');

const VOICE_KEY = 'evarisk.voice';

function readVoice(): boolean {
  try {
    return localStorage.getItem(VOICE_KEY) === 'on';
  } catch {
    return false;
  }
}

// Озвучка выключена по умолчанию: оператору за столом она не нужна. Выбор помнится в этом браузере.
export const voiceOn = ref(readVoice());

export function setVoice(on: boolean): void {
  voiceOn.value = on;
  try {
    localStorage.setItem(VOICE_KEY, on ? 'on' : 'off');
  } catch {
    // Хранилище браузера недоступно: выбор живёт до перезагрузки страницы.
  }
}

// Ответ на более ранний запрос сводки, пришедший позже, не показывается.
const briefingRequests = latestGuard();

// Сводка другого расчёта не остаётся на экране, пока грузится сводка открытого.
function beginBriefing(id: string): number {
  briefingLoading.value = true;
  briefingError.value = '';
  if (!keepsBriefing(briefing.value, id)) briefing.value = null;
  return briefingRequests.begin();
}

// Сводка для экипажа по открытому расчёту; сервер отдаёт её из кеша рядом с расчётом.
export async function loadBriefing(id: string, question: BriefingQuestion = briefingQuestion.value, refresh = false): Promise<void> {
  briefingQuestion.value = question;
  const request = beginBriefing(id);
  try {
    const value = await api.briefing(id, question, voiceOn.value, refresh);
    if (briefingRequests.isCurrent(request)) briefing.value = value;
  } catch (error) {
    if (briefingRequests.isCurrent(request)) {
      briefing.value = null;
      briefingError.value = errorText(error);
    }
  } finally {
    if (briefingRequests.isCurrent(request)) briefingLoading.value = false;
  }
}

// Свой вопрос экипажа: ответ проходит ту же проверку чисел на сервере и занимает место текста сводки;
// прошлые вопросы этого расчёта остаются в короткой истории до смены расчёта, последние ответы модели уходят
// с новым вопросом как контекст (D89).
export interface AskedItem {
  asked: string;
  text: string;
  source: Briefing['source'];
}
export const askedHistory = ref<AskedItem[]>([]);
const ASKED_KEPT = 5;

export async function askOwnQuestion(id: string, text: string): Promise<void> {
  const request = beginBriefing(id);
  try {
    const answer = await api.briefingAsk(id, text, askTurns(askedHistory.value));
    if (!briefingRequests.isCurrent(request)) return;
    briefing.value = answer;
    askedHistory.value = [{ asked: answer.asked || text, text: answer.text, source: answer.source }, ...askedHistory.value].slice(0, ASKED_KEPT);
  } catch (error) {
    if (briefingRequests.isCurrent(request)) briefingError.value = errorText(error);
  } finally {
    if (briefingRequests.isCurrent(request)) briefingLoading.value = false;
  }
}

export const job = ref<Job | null>(null);
export const jobError = ref('');
export const jobRunning = ref(false);
// Идущее задание поставлено автообновлением (тот же запрос): пометка «идёт новый расчёт» над результатом (D85) у него
// не показывается.
export const jobAuto = ref(false);
// Задание, поставленное пользователем: пока оно идёт, показанный результат помечается как прежний (D85).
export const userJobRunning = computed(() => jobRunning.value && !jobAuto.value);

// Постановка задания, опрос прогресса и загрузка результата; ошибки остаются в jobError. auto: пересчёт того же запроса
// автообновлением: результат не сдвигает выбранное время, не пишется в архив браузера и не открывается, если за время
// задания пользователь открыл другой расчёт.
export async function runAssessment(request: AssessmentRequest, options: { auto?: boolean } = {}): Promise<AssessmentResult | null> {
  jobRunning.value = true;
  jobAuto.value = !!options.auto;
  jobError.value = '';
  job.value = null;
  const previous = assessment.value;
  try {
    const created = await api.createJob(request);
    job.value = { id: created.job_id, kind: 'assessment', params: {}, status: 'queued', progress: null, error: null, assessment_id: null, created_at: isoZ(Date.now()), started_at: null, finished_at: null };
    const finished = await waitJob(created.job_id, {
      onProgress: (j) => {
        job.value = j;
        if (j.status === 'queued') loadHealth();
      },
    });
    job.value = finished;
    if (finished.status === 'failed' || !finished.assessment_id) {
      jobError.value = finished.error || 'задание завершилось без результата';
      return null;
    }
    if (options.auto && assessment.value?.id !== previous?.id) return null;
    if (options.auto) autoRefreshedId.value = finished.assessment_id;
    const result = await openAssessment(finished.assessment_id, !options.auto);
    if (result) {
      if (!options.auto) {
        planChange.value = detectPlanChange(previous, result);
        addToArchive(result);
      }
      loadAssessmentList();
    }
    return result;
  } catch (error) {
    jobError.value = errorText(error);
    return null;
  } finally {
    jobRunning.value = false;
    jobAuto.value = false;
  }
}

// Автообновление расчёта «сейчас» (lib/refresh.ts): App.vue вызывает autoRefreshTick по таймеру и при возврате на
// вкладку. Пересчитывается тот же запрос через задание, как по кнопке «Рассчитать».
export const autoRefreshAttempt = ref<number | null>(null);
// Последний расчёт, открытый автообновлением: у него тот же запрос, и форма на /eva не перезаписывает правки пользователя.
export const autoRefreshedId = ref<string | null>(null);

function pageVisible(): boolean {
  return typeof document === 'undefined' || document.visibilityState !== 'hidden';
}

export function autoRefreshState(now: number = Date.now()): RefreshPlan {
  const opened = assessment.value;
  return refreshPlan({
    mode: opened?.request.mode,
    computedAt: opened?.created_at,
    now,
    visible: pageVisible(),
    busy: jobRunning.value || assessmentLoading.value,
    newestData: newestSuccess(sources.value),
    lastAttempt: autoRefreshAttempt.value,
  });
}

export async function autoRefreshTick(now: number = Date.now()): Promise<void> {
  const opened = assessment.value;
  if (!opened || !autoRefreshState(now).due) return;
  autoRefreshAttempt.value = now;
  await runAssessment(opened.request, { auto: true });
}

export const scores = computed(() => (assessment.value ? lineScores(assessment.value) : []));
export const events = computed(() => (assessment.value ? collectEvents(assessment.value) : []));

// Сэмплы для сцены: траектория открытого расчёта, если выбранное время внутри её периода, иначе /trajectory,
// иначе трасса SGP4 по OMM (без освещённости и ЮАА).
export type TrackSource = 'assessment' | 'trajectory' | 'sgp4' | 'none';

export const track = computed<{ source: TrackSource; samples: TrajectorySample[]; label: string; epoch: string | null }>(() => {
  const at = selectedTime.value.getTime();
  const a = assessment.value;
  if (a && a.trajectory.samples.length) {
    const first = ms(a.trajectory.samples[0].t);
    const last = ms(a.trajectory.samples[a.trajectory.samples.length - 1].t);
    if (at >= first && at <= last) {
      return { source: 'assessment', samples: a.trajectory.samples, label: `${a.trajectory.provider.toUpperCase()} · ${a.trajectory.provenance.source}, период расчёта ${a.id}`, epoch: a.trajectory.epoch };
    }
  }
  const t = trajectory.value;
  if (t && t.samples.length) {
    return { source: 'trajectory', samples: t.samples, label: `${t.provider.toUpperCase()} · ${t.provenance.source}${t.is_reconstruction ? ', реконструкция' : ', прогноз'}`, epoch: t.epoch };
  }
  const sat = satrec.value;
  if (sat) {
    const centre = Math.floor(at / (30 * 60000)) * 30 * 60000;
    return { source: 'sgp4', samples: sgp4Track(sat, centre - TRAJ_BACK_MS, centre + TRAJ_AHEAD_MS, 1), label: `SGP4 по OMM ${orbit.value?.provenance?.source || 'celestrak'}`, epoch: orbit.value?.omm?.EPOCH || null };
  }
  return { source: 'none', samples: [], label: 'траектория недоступна', epoch: null };
});

export interface StationState {
  point: GeoPoint | null;
  sample: TrajectorySample | null;
  positionSource: 'sgp4' | 'trajectory' | 'none';
  speedSource: 'sgp4' | 'samples' | 'none';
}

// SGP4 по OMM пригоден только возле эпохи элементов: за неделю ошибка вдоль орбиты уже десятки километров,
// а за годы положение теряет смысл.
const SGP4_VALID_MS = 7 * 24 * 3600000;

// Положение станции на выбранное время: по сэмплам трассы эфемериды (расчёта или /trajectory) с интерполяцией;
// SGP4 по OMM только когда трассы эфемериды нет и выбранное время не дальше недели от эпохи элементов.
// Ближайший сэмпл даёт освещённость, магнитную широту и ЮАА.
export const station = computed<StationState>(() => {
  const at = selectedTime.value.getTime();
  const samples = track.value.samples;
  const sample = samples.length ? nearestSample(samples, at) : null;
  const sampleClose = sample && Math.abs(ms(sample.t) - at) <= 2 * 60000 ? sample : null;
  if (track.value.source !== 'sgp4') {
    const p = interpolatePoint(samples, at);
    if (p) {
      return {
        point: { lat: p.lat, lon: p.lon, alt: p.alt, speed: speedFromSamples(samples, p.index) },
        sample: sampleClose,
        positionSource: 'trajectory',
        speedSource: 'samples',
      };
    }
  }
  const sat = satrec.value;
  const epoch = orbit.value?.omm?.EPOCH ? ms(orbit.value.omm.EPOCH) : null;
  const sgp4 = sat && epoch !== null && Math.abs(at - epoch) <= SGP4_VALID_MS ? propagateGeo(sat, new Date(at)) : null;
  if (sgp4) {
    return { point: sgp4, sample: sampleClose, positionSource: 'sgp4', speedSource: 'sgp4' };
  }
  return { point: null, sample: null, positionSource: 'none', speedSource: 'none' };
});

export const evidence = ref<Evidence | null>(null);
export const evidenceError = ref('');
export const evidenceOpen = ref(false);
export const evidenceLoading = ref(false);

export async function openEvidence(rawId: string): Promise<void> {
  evidenceOpen.value = true;
  evidenceLoading.value = true;
  evidence.value = null;
  evidenceError.value = '';
  try {
    evidence.value = await api.evidence(rawId);
  } catch (error) {
    evidenceError.value = errorText(error);
  } finally {
    evidenceLoading.value = false;
  }
}

export function closeEvidence(): void {
  evidenceOpen.value = false;
}

export const toast = ref('');
let toastTimer: ReturnType<typeof setTimeout> | undefined;

export function notify(text: string): void {
  toast.value = text;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (toast.value = ''), 4000);
}

export interface ArchiveItem {
  id: string;
  savedAt: string;
  request: AssessmentRequest;
  algorithm_version: string;
  preferred: { start: string; end: string } | null;
  insufficient: boolean;
}

const ARCHIVE_KEY = 'evarisk.archive';

function readArchive(): ArchiveItem[] {
  try {
    const value = JSON.parse(localStorage.getItem(ARCHIVE_KEY) || '[]');
    return Array.isArray(value) ? value : [];
  } catch {
    return [];
  }
}

export const archive = ref<ArchiveItem[]>(readArchive());

function writeArchive(): boolean {
  try {
    localStorage.setItem(ARCHIVE_KEY, JSON.stringify(archive.value));
    return true;
  } catch {
    return false;
  }
}

export function addToArchive(result: AssessmentResult): boolean {
  const item: ArchiveItem = {
    id: result.id,
    savedAt: isoZ(Date.now()),
    request: result.request,
    algorithm_version: result.algorithm_version,
    preferred: result.recommendation.preferred,
    insufficient: result.recommendation.insufficient,
  };
  archive.value = [item, ...archive.value.filter((entry) => entry.id !== result.id)].slice(0, 100);
  return writeArchive();
}

export function removeFromArchive(id: string): void {
  archive.value = archive.value.filter((entry) => entry.id !== id);
  writeArchive();
}

// Перезагрузка траектории при сдвиге выбранного времени за пределы загруженного получаса.
watch(selectedTime, () => loadTrajectory());

// Запросы при старте приложения; вызывается один раз из App.vue.
export async function bootstrap(): Promise<void> {
  await Promise.all([loadHealth(), loadSources(), loadOrbit(), loadRules(), loadTrajectory(), restoreAssessment()]);
}
