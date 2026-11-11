// Типы повторяют src/evarisk/api/schemas.py и src/evarisk/types.py; времена в ответах ISO-8601 с суффиксом Z.

export type Kind = 'observation' | 'external_forecast' | 'computed';
export type PublishedAtKind = 'exact' | 'derived' | 'unknown';
export type Availability = 'available' | 'missing' | 'stale' | 'unusable_for_replay';
export type Level = 'none' | 'elevated' | 'high' | 'critical' | 'unknown';
export type Mode = 'current' | 'review' | 'replay';
export type SourceStateName = 'active' | 'frozen' | 'disabled';
export type JobState = 'queued' | 'running' | 'done' | 'failed';
export type Grade = 'low' | 'medium' | 'high';

export interface Provenance {
  raw_id: string;
  source: string;
  product: string;
  published_at: string | null;
  published_at_kind: PublishedAtKind;
  data_version: string;
  url: string;
}

export interface Interval {
  start: string;
  end: string;
  level: Level;
  kind: Kind;
  provenance: Provenance[];
  rule: string;
  note: string;
  start_uncertainty: number | string | null;
  end_uncertainty: number | string | null;
}

export interface Confidence {
  grade: Grade;
  depends_on: string;
}

export interface LineAssessment {
  line: string;
  mechanism: string;
  intervals: Interval[];
  availability: Availability;
  confidence: Confidence;
  limitations: string[];
  chain_ids: string[];
  metrics: Record<string, number>;
}

export interface Window {
  start: string;
  end: string;
}

export interface WindowScore {
  window: Window;
  per_line: Record<string, Record<Level, number>>;
  completeness: number;
  excluded_reason: string | null;
}

export interface Recommendation {
  preferred: Window | null;
  reason: string;
  equivalents: Window[];
  insufficient: boolean;
  rules_applied: string[];
  // Решение и пояснения обычными предложениями из тех же чисел; первое предложение это решение. В расчётах
  // прежних версий поля нет.
  explanation?: string[];
}

export interface Constraints {
  min_sunlit_fraction: number | null;
  start_in_daylight: boolean;
}

export interface AssessmentRequest {
  mode: Mode;
  cutoff: string | null;
  eva_start: string;
  duration_hours: number;
  search_start: string;
  search_end: string;
  constraints?: Constraints | null;
}

export interface TrajectorySample {
  t: string;
  lat_deg: number;
  lon_deg: number;
  alt_km: number;
  sunlit: boolean;
  mlat_deg: number | null;
  in_saa: boolean;
  level: Level;
}

export interface SaaPass {
  start: string;
  end: string;
  min_b_nt: number;
  min_at: string;
  threshold_nt: number;
  rule: string;
  note: string;
  provenance: Provenance[];
}

export interface TrajectorySummary {
  provider: string;
  epoch: string;
  is_reconstruction: boolean;
  provenance: Provenance;
  age_at_query_hours: number | null;
  samples: TrajectorySample[];
  // Расчёты, сохранённые до появления поля, его не содержат.
  saa_passes?: SaaPass[];
}

export interface Verification {
  truth_asof: null;
  line: string;
  predicted: Interval[];
  observed: Interval[];
  observed_availability: Availability;
  observed_preferred: Window | null;
  observed_insufficient: boolean;
  lead_time_minutes: number | null;
  first_observed_alarm: string | null;
  first_predicted_warning: string | null;
  hit_minutes: number;
  miss_minutes: number;
  false_alarm_minutes: number;
  // Экспозиция экипажа: минуты геомагнитного доступа с уровнем ≥ high; у расчётов до D61 этих полей нет.
  exposure_hit_minutes?: number | null;
  exposure_miss_minutes?: number | null;
  exposure_false_alarm_minutes?: number | null;
  quiet_minutes: number;
  unverified_minutes: number;
  predicted_unknown_minutes: number;
  window_choice_stable: boolean;
  note: string;
  // Минуты экспозиции по факту внутри запрошенного и предпочтительного окна; у ранних расчётов полей нет.
  requested_exposure_minutes?: number | null;
  preferred_exposure_minutes?: number | null;
  // Простые подходы, сверенные с тем же фактом той же функцией; у ранних расчётов поля нет.
  baselines?: BaselineCheck[];
}

export type BaselineName = 'persistence' | 'single_alert' | 'swpc_outlook';

// Простой подход в сверке replay: один уровень на весь период поиска без учёта орбиты.
export interface BaselineCheck {
  name: BaselineName;
  title: string;
  rule: string;
  level: Level;
  basis: string;
  provenance: Provenance[];
  preferred: Window | null;
  insufficient: boolean;
  preferred_exposure_minutes: number | null;
  exposure_hit_minutes: number | null;
  exposure_miss_minutes: number | null;
  exposure_false_alarm_minutes: number | null;
  lead_time_minutes: number | null;
  window_choice_stable: boolean;
  note: string;
}

export interface AssessmentResult {
  id: string;
  created_at: string;
  request: AssessmentRequest;
  asof: string | null;
  algorithm_version: string;
  trajectory: TrajectorySummary;
  lines: LineAssessment[];
  requested_window: WindowScore;
  windows: WindowScore[];
  recommendation: Recommendation;
  sources_used: Provenance[];
  data_availability: Record<string, Availability>;
  limitations: string[];
  rules: string[];
  verification: Verification | null;
  geometry?: AccessGeometry | null;
}

// Страховка по геометрии (D61): минуты возможного доступа протонов при наибольшем Kp, информационно.
export interface AccessGeometry {
  kp: number;
  requested_minutes: number;
  preferred_minutes: number | null;
  safest: { start: string; end: string } | null;
  safest_minutes: number | null;
  note: string;
}

export interface AssessmentListItem {
  id: string;
  created_at: string;
  mode: Mode;
  eva_start: string;
}

export type BriefingQuestion = 'summary' | 'can_go_now' | 'when_better' | 'what_blocks';
export type BriefingSource = 'llm' | 'template';
export type BriefingVerdict = 'keep_requested' | 'move_to' | 'insufficient';

export interface BriefingWindow {
  start: string;
  end: string;
  duration_hours: number;
  // Минуты не посчитаны, когда в сравнении окон нет ни одной линии: это null, не ноль. Это суммы по линиям штрафа
  // (линии идут одновременно, сумма бывает больше длины окна); показываются только у сводок без полей *_by_line.
  exposure_minutes: number | null;
  unknown_minutes: number | null;
  // Минуты по каждой линии штрафа (D84), каждое число не больше длины окна; у сводок до D84 пусто или нет поля.
  exposure_by_line?: Record<string, number>;
  unknown_by_line?: Record<string, number>;
  elevated_by_line?: Record<string, number>;
}

export interface BriefingExposed {
  start: string;
  end: string;
  line: string;
  level: Level;
  minutes: number;
}

export interface BriefingGap {
  line: string;
  availability: Availability;
}

export interface BriefingFacts {
  assessment_id: string;
  question: BriefingQuestion;
  mode: Mode;
  data_moment: string | null;
  cutoff: string | null;
  basis: string;
  verdict: BriefingVerdict;
  requested: BriefingWindow;
  recommended: BriefingWindow | null;
  exposed: BriefingExposed[];
  exposed_total: number;
  clear_after: string | null;
  gaps: BriefingGap[];
  disabled_sources: string[];
  confidence_grade: Grade | null;
  confidence_depends_on: string | null;
  marginal_note: string | null;
  windows_total: number;
  windows_excluded: number;
  provenance: Provenance[];
  algorithm_version: string;
  allowed_values: string[];
}

export interface Briefing {
  assessment_id: string;
  question: BriefingQuestion;
  language: string;
  text: string;
  speech_text: string;
  source: BriefingSource;
  model: string | null;
  validated: boolean;
  violations: string[];
  facts: BriefingFacts;
  generated_at: string;
  algorithm_version: string;
  audio_url: string | null;
  voice_error: string | null;
  // Текст своего вопроса экипажа; у быстрых вопросов поля нет.
  asked?: string | null;
}

export interface JobProgress {
  step: string;
  fraction: number;
  at?: string;
  note?: string;
}

export interface Job {
  id: string;
  kind: string;
  params: Record<string, unknown>;
  status: JobState;
  progress: JobProgress | null;
  error: string | null;
  assessment_id: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface JobCreated {
  job_id: string;
  status: 'queued';
}

export interface SourceState {
  name: string;
  products: string[];
  // Архивный источник без адаптера обновления: частоты нет, отключить можно.
  cadence_s: number | null;
  archive_only: boolean;
  supports_archive: boolean;
  state: SourceStateName;
  reason: string | null;
  changed_at: string | null;
  last_attempt_at: string | null;
  last_success_at: string | null;
  last_error: string | null;
  last_record_fetched_at: string | null;
  records: number;
}

export interface Health {
  status: string;
  raw_root: string;
  worker_last_tick: string | null;
}

export interface EvidenceRecord {
  id: string;
  source: string;
  product: string;
  url: string;
  fetched_at: string;
  published_at: string | null;
  published_at_kind: PublishedAtKind;
  data_version: string;
  sha256: string;
  path: string;
  size: number;
  content_type: string;
  http_status: number;
  notes: string;
}

export interface Evidence {
  record: EvidenceRecord;
  content_text: string | null;
  download_url: string;
}

export interface Trajectory {
  provider: string;
  epoch: string;
  is_reconstruction: boolean;
  provenance: Provenance;
  age_at_query_hours: number | null;
  samples: TrajectorySample[];
  limitations: string[];
}

// OMM в формате CelesTrak; числовые поля могут приходить строками, satellite.js их принимает.
export interface Omm {
  OBJECT_NAME: string;
  OBJECT_ID: string;
  EPOCH: string;
  MEAN_MOTION: number | string;
  ECCENTRICITY: number | string;
  INCLINATION: number | string;
  RA_OF_ASC_NODE: number | string;
  ARG_OF_PERICENTER: number | string;
  MEAN_ANOMALY: number | string;
  EPHEMERIS_TYPE?: 0 | '0';
  CLASSIFICATION_TYPE?: 'U' | 'C';
  NORAD_CAT_ID: number | string;
  ELEMENT_SET_NO: number | string;
  REV_AT_EPOCH?: number | string;
  BSTAR: number | string;
  MEAN_MOTION_DOT: number | string;
  MEAN_MOTION_DDOT: number | string;
  [key: string]: unknown;
}

export interface OrbitLatest {
  omm: Omm | null;
  provenance: Provenance | null;
  oem: { creation_date: string; start_time: string; stop_time: string; provenance: Provenance } | null;
}

// Последний отчёт CelesTrak SOCRATES по МКС независимо от расчёта (GET /conjunctions/latest). Уровень строки по
// MAXPROB как верхней оценке Pc (D53); со-орбитальные объекты (пристыкованные корабли) в список не входят и
// посчитаны в coorbiting_rows; stale, когда отчёт старше max_report_age_hours.
export type ConjunctionLevel = 'none' | 'elevated' | 'high';

export interface ConjunctionRow {
  tca: string;
  secondary_name: string;
  secondary_catnr: number;
  min_range_km: number;
  rel_speed_kms: number;
  max_probability: number;
  level: ConjunctionLevel;
  coorbiting: boolean;
}

export interface ConjunctionsLatest {
  available: boolean;
  data_as_of: string | null;
  age_hours: number | null;
  stale: boolean;
  max_report_age_hours: number;
  interval_start: string | null;
  interval_stop: string | null;
  provenance: Provenance | null;
  conjunctions: ConjunctionRow[];
  coorbiting_rows: number;
  limitations: string[];
}

export interface Rules {
  windows: {
    step_minutes: number;
    weight_high: number;
    weight_elevated: number;
    equivalence_tolerance_hours: number;
    critical_excludes: boolean;
    [key: string]: unknown;
  };
  sep: Record<string, unknown>;
  sources: Record<string, unknown>;
  [key: string]: unknown;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

// Ошибки API имеют вид {"error": {"code", "message"}}; иной ответ описывается HTTP-статусом.
export async function parseError(response: Response): Promise<ApiError> {
  const contentType = response.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    try {
      const data = await response.json();
      if (data && data.error && typeof data.error.message === 'string') {
        return new ApiError(response.status, String(data.error.code || 'error'), data.error.message);
      }
      if (data && typeof data.detail === 'string') {
        return new ApiError(response.status, 'detail', data.detail);
      }
    } catch {
      // Тело не разобрано: ниже используется статус.
    }
  }
  return new ApiError(response.status, 'http', `HTTP ${response.status}`);
}

export function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message === 'Failed to fetch' ? 'API недоступен' : error.message;
  return String(error);
}

export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

let fetchImpl: FetchLike = (input, init) => fetch(input, init);

export function setFetch(impl: FetchLike): void {
  fetchImpl = impl;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetchImpl(url, init);
  if (!response.ok) throw await parseError(response);
  return (await response.json()) as T;
}

function postJson<T>(url: string, body?: unknown): Promise<T> {
  return request<T>(url, {
    method: 'POST',
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export const api = {
  health: () => request<Health>('/health'),
  sources: () => request<SourceState[]>('/sources'),
  setSourceState: (name: string, state: SourceStateName, reason: string) =>
    postJson<SourceState>(`/sources/${encodeURIComponent(name)}/state`, { state, reason }),
  refreshSource: (name: string) => postJson<JobCreated>(`/sources/${encodeURIComponent(name)}/refresh`),
  createJob: (body: AssessmentRequest) => postJson<JobCreated>('/jobs', body),
  job: (id: string) => request<Job>(`/jobs/${encodeURIComponent(id)}`),
  assessments: () => request<AssessmentListItem[]>('/assessments'),
  assessment: (id: string) => request<AssessmentResult>(`/assessments/${encodeURIComponent(id)}`),
  evidence: (rawId: string) => request<Evidence>(`/evidence/${encodeURIComponent(rawId)}`),
  briefing: (id: string, question: BriefingQuestion = 'summary', voice = false, refresh = false) =>
    request<Briefing>(
      `/assessments/${encodeURIComponent(id)}/briefing?question=${question}&language=ru&voice=${voice}&refresh=${refresh}`,
    ),
  briefingAsk: (id: string, text: string, history: { asked: string; answer: string }[] = []) =>
    postJson<Briefing>(`/assessments/${encodeURIComponent(id)}/briefing/ask`, { text, language: 'ru', history }),
  briefingLatest: (voice = false) => request<Briefing>(`/briefing/latest?voice=${voice}`),
  trajectory: (start: string, end: string, stepMinutes = 1, mode: 'current' | 'review' = 'current') =>
    request<Trajectory>(
      `/trajectory?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}&step_minutes=${stepMinutes}&mode=${mode}`,
    ),
  orbitLatest: () => request<OrbitLatest>('/orbit/latest'),
  conjunctionsLatest: () => request<ConjunctionsLatest>('/conjunctions/latest'),
  rules: () => request<Rules>('/rules'),
};

export const exportUrl = (id: string) => `/assessments/${encodeURIComponent(id)}/export`;
export const jsonUrl = (id: string) => `/assessments/${encodeURIComponent(id)}`;
export const downloadUrl = (rawId: string) => `/evidence/${encodeURIComponent(rawId)}/download`;

export const POLL_MS = 1500;

export interface PollOptions {
  onProgress?: (job: Job) => void;
  sleep?: (ms: number) => Promise<void>;
  signal?: AbortSignal;
}

const defaultSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

// Опрос задания до состояния done или failed; между запросами пауза POLL_MS.
export async function waitJob(jobId: string, options: PollOptions = {}): Promise<Job> {
  const sleep = options.sleep || defaultSleep;
  for (;;) {
    if (options.signal?.aborted) throw new Error('опрос задания прерван');
    const job = await api.job(jobId);
    if (job.status === 'done' || job.status === 'failed') return job;
    options.onProgress?.(job);
    await sleep(POLL_MS);
  }
}
