import { getCurrentScope, onScopeDispose, ref, type Ref } from 'vue';

// Голосовой ввод своего вопроса экипажа через Web Speech API браузера (SpeechRecognition, в Chrome и Edge
// webkitSpeechRecognition). Распознаёт речь сам браузер: в Chrome звук уходит в облачный сервис Google, в Edge в
// сервис Microsoft; на сервер evarisk звук не передаётся, уходит только текст вопроса после нажатия «Спросить».

export const VOICE_UNSUPPORTED = 'Голосовой ввод не поддерживается этим браузером';
export const VOICE_PRIVACY =
  'Речь распознаёт браузер: в Chrome звук уходит в облачный сервис Google, в Edge в сервис Microsoft. ' +
  'На наш сервер звук не передаётся, только текст вопроса после нажатия «Спросить».';
// Страховка на случай, если браузер не завершил распознавание сам после паузы.
export const VOICE_MAX_MS = 20_000;

export type VoiceState = 'idle' | 'listening' | 'error';

// Минимум интерфейса SpeechRecognition, который здесь нужен; тесты подставляют свой объект.
export interface RecognitionAlternative { transcript: string }
export interface RecognitionResult { readonly length: number; isFinal: boolean; [index: number]: RecognitionAlternative }
export interface RecognitionResultEvent { results: { readonly length: number; [index: number]: RecognitionResult } }
export interface RecognitionErrorEvent { error: string }
export interface RecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  onresult: ((event: RecognitionResultEvent) => void) | null;
  onerror: ((event: RecognitionErrorEvent) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}
export type RecognitionCtor = new () => RecognitionLike;

// Конструктор распознавания браузера или null (Firefox, Safari без поддержки, тесты без window).
export function recognitionCtor(scope: unknown = typeof window === 'undefined' ? undefined : window): RecognitionCtor | null {
  if (!scope) return null;
  const s = scope as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor };
  return s.SpeechRecognition || s.webkitSpeechRecognition || null;
}

// Текст всех результатов сессии подряд: при continuous = false это одна фраза, промежуточная или итоговая.
export function transcriptOf(event: RecognitionResultEvent): { text: string; final: boolean } {
  let text = '';
  let final = event.results.length > 0;
  for (let i = 0; i < event.results.length; i++) {
    const result = event.results[i];
    text += result.length > 0 ? result[0].transcript : '';
    if (!result.isFinal) final = false;
  }
  return { text: text.replace(/\s+/g, ' ').trim(), final };
}

// Код ошибки SpeechRecognitionErrorEvent в короткую фразу; aborted это остановка пользователем, не ошибка.
export function voiceErrorText(code: string): string | null {
  switch (code) {
    case 'aborted':
      return null;
    case 'not-allowed':
    case 'service-not-allowed':
      return 'Нет доступа к микрофону: разрешите его в настройках сайта; по http микрофон доступен только на localhost.';
    case 'audio-capture':
      return 'Микрофон не найден: подключите его или введите вопрос текстом.';
    case 'no-speech':
      return 'Речь не услышана: нажмите на микрофон и говорите сразу.';
    case 'network':
      return 'Нет связи со службой распознавания браузера: проверьте интернет или введите вопрос текстом.';
    case 'language-not-supported':
      return 'Браузер не распознаёт русскую речь: введите вопрос текстом.';
    default:
      return 'Распознать речь не удалось: введите вопрос текстом.';
  }
}

export interface VoiceInputOptions {
  ctor?: RecognitionCtor | null;
  lang?: string;
  maxLength?: number;
  maxMs?: number;
  // Текст по ходу речи (final = false) и итоговый (final = true).
  onText: (text: string, final: boolean) => void;
}

export interface VoiceInput {
  supported: boolean;
  state: Ref<VoiceState>;
  error: Ref<string>;
  start(): void;
  stop(): void;
  toggle(): void;
  dispose(): void;
}

// Одна фраза на нажатие: continuous = false, браузер сам завершает распознавание после паузы в речи.
export function useVoiceInput(options: VoiceInputOptions): VoiceInput {
  const ctor = options.ctor === undefined ? recognitionCtor() : options.ctor;
  const state = ref<VoiceState>('idle');
  const error = ref('');
  let current: RecognitionLike | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;

  function clearTimer(): void {
    if (timer !== null) clearTimeout(timer);
    timer = null;
  }

  function fail(code: string): void {
    const text = voiceErrorText(code);
    if (text === null) return;
    state.value = 'error';
    error.value = text;
  }

  function start(): void {
    if (!ctor || state.value === 'listening') return;
    const recognition = new ctor();
    recognition.lang = options.lang ?? 'ru-RU';
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;
    let errorCode = '';
    recognition.onresult = (event) => {
      if (current !== recognition) return;
      const { text, final } = transcriptOf(event);
      if (text) options.onText(options.maxLength ? text.slice(0, options.maxLength) : text, final);
    };
    recognition.onerror = (event) => {
      if (current === recognition) errorCode = event.error;
    };
    recognition.onend = () => {
      if (current !== recognition) return;
      current = null;
      clearTimer();
      state.value = 'idle';
      if (errorCode) fail(errorCode);
    };
    current = recognition;
    error.value = '';
    state.value = 'listening';
    try {
      recognition.start();
    } catch {
      current = null;
      fail('start-failed');
      return;
    }
    timer = setTimeout(stop, options.maxMs ?? VOICE_MAX_MS);
  }

  // Остановка с распознаванием уже сказанного: итоговый результат придёт до onend.
  function stop(): void {
    current?.stop();
  }

  function toggle(): void {
    if (state.value === 'listening') stop();
    else start();
  }

  // Прерывание без результата: смена экрана или расчёта.
  function dispose(): void {
    const recognition = current;
    current = null;
    clearTimer();
    recognition?.abort();
    if (state.value === 'listening') state.value = 'idle';
  }

  if (getCurrentScope()) onScopeDispose(dispose);

  return { supported: !!ctor, state, error, start, stop, toggle, dispose };
}
