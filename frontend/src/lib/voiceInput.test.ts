import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { effectScope } from 'vue';
import {
  VOICE_MAX_MS,
  recognitionCtor,
  transcriptOf,
  useVoiceInput,
  voiceErrorText,
  type RecognitionErrorEvent,
  type RecognitionLike,
  type RecognitionResultEvent,
} from './voiceInput';

// Поддельный SpeechRecognition: запоминает созданные экземпляры, тест сам вызывает onresult, onerror и onend,
// как это делает браузер.
class FakeRecognition implements RecognitionLike {
  static instances: FakeRecognition[] = [];
  static failStart = false;
  lang = '';
  continuous = true;
  interimResults = false;
  maxAlternatives = 0;
  onresult: ((event: RecognitionResultEvent) => void) | null = null;
  onerror: ((event: RecognitionErrorEvent) => void) | null = null;
  onend: (() => void) | null = null;
  started = 0;
  stopped = 0;
  aborted = 0;
  constructor() {
    FakeRecognition.instances.push(this);
  }
  start(): void {
    if (FakeRecognition.failStart) throw new Error('InvalidStateError');
    this.started++;
  }
  stop(): void {
    this.stopped++;
  }
  abort(): void {
    this.aborted++;
  }
  say(parts: Array<[string, boolean]>): void {
    const results = parts.map(([transcript, isFinal]) => Object.assign([{ transcript }], { isFinal }));
    this.onresult?.({ results });
  }
  fail(error: string): void {
    this.onerror?.({ error });
  }
  end(): void {
    this.onend?.();
  }
}

beforeEach(() => {
  FakeRecognition.instances = [];
  FakeRecognition.failStart = false;
});
afterEach(() => vi.useRealTimers());

function setup(maxLength?: number) {
  const texts: Array<[string, boolean]> = [];
  const voice = useVoiceInput({ ctor: FakeRecognition, maxLength, onText: (text, final) => texts.push([text, final]) });
  return { voice, texts, rec: () => FakeRecognition.instances[FakeRecognition.instances.length - 1] };
}

describe('recognitionCtor', () => {
  it('берёт SpeechRecognition, иначе webkitSpeechRecognition', () => {
    expect(recognitionCtor({ SpeechRecognition: FakeRecognition, webkitSpeechRecognition: class {} })).toBe(FakeRecognition);
    expect(recognitionCtor({ webkitSpeechRecognition: FakeRecognition })).toBe(FakeRecognition);
  });

  it('без API и без window возвращает null', () => {
    expect(recognitionCtor({})).toBeNull();
    expect(recognitionCtor(undefined)).toBeNull();
  });
});

describe('transcriptOf', () => {
  it('склеивает результаты и отмечает итоговый только при всех итоговых', () => {
    const results = [Object.assign([{ transcript: 'на каких витках' }], { isFinal: true }), Object.assign([{ transcript: '  высокий  уровень ' }], { isFinal: false })];
    expect(transcriptOf({ results })).toEqual({ text: 'на каких витках высокий уровень', final: false });
    results[1].isFinal = true;
    expect(transcriptOf({ results }).final).toBe(true);
    expect(transcriptOf({ results: [] })).toEqual({ text: '', final: false });
  });
});

describe('voiceErrorText', () => {
  it('переводит коды ошибок в короткие фразы', () => {
    expect(voiceErrorText('not-allowed')).toMatch(/^Нет доступа к микрофону/);
    expect(voiceErrorText('service-not-allowed')).toMatch(/^Нет доступа к микрофону/);
    expect(voiceErrorText('no-speech')).toMatch(/^Речь не услышана/);
    expect(voiceErrorText('network')).toMatch(/^Нет связи со службой распознавания/);
    expect(voiceErrorText('audio-capture')).toMatch(/^Микрофон не найден/);
    expect(voiceErrorText('something-new')).toMatch(/^Распознать речь не удалось/);
  });

  it('остановка пользователем не ошибка', () => {
    expect(voiceErrorText('aborted')).toBeNull();
  });
});

describe('useVoiceInput', () => {
  it('без API браузера не поддерживается и ничего не запускает', () => {
    const voice = useVoiceInput({ ctor: null, onText: () => undefined });
    expect(voice.supported).toBe(false);
    voice.toggle();
    expect(voice.state.value).toBe('idle');
    expect(FakeRecognition.instances).toHaveLength(0);
  });

  it('запускает распознавание одной фразы на русском', () => {
    const { voice, rec } = setup();
    expect(voice.supported).toBe(true);
    voice.start();
    expect(rec().started).toBe(1);
    expect(rec().lang).toBe('ru-RU');
    expect(rec().continuous).toBe(false);
    expect(rec().interimResults).toBe(true);
    expect(voice.state.value).toBe('listening');
    voice.start();
    expect(FakeRecognition.instances).toHaveLength(1);
  });

  it('передаёт промежуточный и итоговый текст, после конца речи возвращается в покой', () => {
    const { voice, texts, rec } = setup();
    voice.start();
    rec().say([['на каких', false]]);
    rec().say([['на каких витках высокий уровень', true]]);
    rec().end();
    expect(texts).toEqual([
      ['на каких', false],
      ['на каких витках высокий уровень', true],
    ]);
    expect(voice.state.value).toBe('idle');
    expect(voice.error.value).toBe('');
  });

  it('обрезает текст до длины поля', () => {
    const { voice, texts, rec } = setup(5);
    voice.start();
    rec().say([['протоны высокие', true]]);
    expect(texts).toEqual([['прото', true]]);
  });

  it('повторное нажатие останавливает прослушивание', () => {
    const { voice, rec } = setup();
    voice.toggle();
    voice.toggle();
    expect(rec().stopped).toBe(1);
    rec().end();
    expect(voice.state.value).toBe('idle');
  });

  it('ошибка браузера даёт состояние error с фразой, новое нажатие её снимает', () => {
    const { voice, rec } = setup();
    voice.start();
    rec().fail('not-allowed');
    rec().end();
    expect(voice.state.value).toBe('error');
    expect(voice.error.value).toMatch(/^Нет доступа к микрофону/);
    voice.start();
    expect(voice.state.value).toBe('listening');
    expect(voice.error.value).toBe('');
  });

  it('тишина: no-speech и network дают свои фразы', () => {
    const { voice, rec } = setup();
    voice.start();
    rec().fail('no-speech');
    rec().end();
    expect(voice.error.value).toMatch(/^Речь не услышана/);
    voice.start();
    rec().fail('network');
    rec().end();
    expect(voice.error.value).toMatch(/^Нет связи/);
  });

  it('aborted не показывается как ошибка', () => {
    const { voice, rec } = setup();
    voice.start();
    rec().fail('aborted');
    rec().end();
    expect(voice.state.value).toBe('idle');
    expect(voice.error.value).toBe('');
  });

  it('исключение при старте даёт ошибку, а не вечное прослушивание', () => {
    FakeRecognition.failStart = true;
    const { voice } = setup();
    voice.start();
    expect(voice.state.value).toBe('error');
    expect(voice.error.value).toMatch(/^Распознать речь не удалось/);
  });

  it('останавливает прослушивание по страховочному таймеру', () => {
    vi.useFakeTimers();
    const { voice, rec } = setup();
    voice.start();
    vi.advanceTimersByTime(VOICE_MAX_MS - 1);
    expect(rec().stopped).toBe(0);
    vi.advanceTimersByTime(1);
    expect(rec().stopped).toBe(1);
  });

  it('при уходе с экрана прерывает распознавание и игнорирует поздние события', () => {
    const texts: string[] = [];
    const scope = effectScope();
    const voice = scope.run(() => useVoiceInput({ ctor: FakeRecognition, onText: (text) => texts.push(text) }))!;
    voice.start();
    const rec = FakeRecognition.instances[0];
    scope.stop();
    expect(rec.aborted).toBe(1);
    expect(voice.state.value).toBe('idle');
    rec.say([['поздно', true]]);
    rec.end();
    expect(texts).toEqual([]);
  });
});
