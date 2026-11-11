<script setup lang="ts">
// Кнопка микрофона у поля своего вопроса в сводке для экипажа: распознанный текст подставляется в поле,
// отправляет вопрос пользователь кнопкой «Спросить» или Enter (фокус переходит в поле). Логика в lib/voiceInput.ts.
import { Mic, MicOff, Square } from 'lucide-vue-next';
import { computed, ref } from 'vue';
import { VOICE_PRIVACY, VOICE_UNSUPPORTED, useVoiceInput } from '../lib/voiceInput';

const props = defineProps<{ maxLength?: number }>();
const emit = defineEmits<{ text: [value: string] }>();
const button = ref<HTMLButtonElement | null>(null);

const voice = useVoiceInput({
  maxLength: props.maxLength,
  onText: (text, final) => {
    emit('text', text);
    if (final) button.value?.closest('form')?.querySelector<HTMLInputElement>('input[type="text"]')?.focus();
  },
});
const { supported, state, error } = voice;
const listening = computed(() => state.value === 'listening');
const title = computed(() => {
  if (!supported) return VOICE_UNSUPPORTED;
  if (listening.value) return `Остановить запись. ${VOICE_PRIVACY}`;
  return `Спросить голосом: вопрос появится в поле, отправка кнопкой «Спросить». ${VOICE_PRIVACY}`;
});
const status = computed(() => {
  if (listening.value) return 'Слушаю: задайте вопрос, запись остановится сама после паузы.';
  if (state.value === 'error') return error.value;
  return '';
});
</script>

<template>
  <span class="voice-ask" :title="supported ? undefined : VOICE_UNSUPPORTED">
    <button ref="button" type="button" class="button voice-button" :class="state" :disabled="!supported" :aria-pressed="listening" :aria-label="listening ? 'Остановить запись вопроса' : 'Спросить голосом'" :title="title" @click="voice.toggle">
      <template v-if="listening"><span class="voice-dot" aria-hidden="true"></span><Square :size="13" :stroke-width="1.65" aria-hidden="true" />Стоп</template>
      <MicOff v-else-if="!supported || state === 'error'" :size="15" :stroke-width="1.65" aria-hidden="true" />
      <Mic v-else :size="15" :stroke-width="1.65" aria-hidden="true" />
    </button>
  </span>
  <p v-if="status" class="voice-status" :class="state" role="status" aria-live="polite">{{ status }}</p>
</template>

<style>
.voice-ask{display:inline-flex;flex:none}
.voice-button.listening{border-color:var(--red);color:var(--red)}
.voice-button.error{border-color:#6b3a38;color:var(--red)}
.voice-dot{width:8px;height:8px;border-radius:50%;background:var(--red);animation:voice-pulse 1.1s ease-in-out infinite}
.briefing-ask:has(> .voice-status){flex-wrap:wrap}
.voice-status{flex-basis:100%;order:5;font-size:12px;color:#9fb4c4}
.voice-status.error{color:var(--red)}
@keyframes voice-pulse{50%{opacity:.25;transform:scale(.7)}}
@media (prefers-reduced-motion:reduce){.voice-dot{animation:none}}
</style>
