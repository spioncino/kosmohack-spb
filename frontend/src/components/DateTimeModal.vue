<script setup lang="ts">
import { ref, watch } from 'vue';
import { fromLocalInput } from '../lib/format';
import Icon from './Icon.vue';

const props = defineProps<{ open: boolean; value: Date; title?: string }>();
const emit = defineEmits<{ close: []; select: [value: Date] }>();

const date = ref('');
const time = ref('');
const error = ref('');

watch(
  () => [props.open, props.value] as const,
  () => {
    const iso = props.value.toISOString();
    date.value = iso.slice(0, 10);
    time.value = iso.slice(11, 16);
    error.value = '';
  },
  { immediate: true },
);

function apply(): void {
  const parsed = fromLocalInput(`${date.value}T${time.value || '00:00'}`);
  if (!parsed) {
    error.value = 'Дата или время заданы неверно';
    return;
  }
  emit('select', new Date(parsed));
  emit('close');
}

function now(): void {
  const current = new Date(Math.floor(Date.now() / 60000) * 60000);
  date.value = current.toISOString().slice(0, 10);
  time.value = current.toISOString().slice(11, 16);
}
</script>
<template>
  <div v-if="open" class="modal-backdrop" @click.self="emit('close')">
    <section class="panel help-modal datetime-modal" role="dialog" aria-modal="true" :aria-label="title || 'Выбор времени расчёта'">
      <button class="icon-btn close-modal" aria-label="Закрыть" @click="emit('close')"><Icon name="X" /></button>
      <span class="eyebrow">ВРЕМЯ РАСЧЁТА · UTC</span>
      <h2>{{ title || 'Выбранное время' }}</h2>
      <p>Момент, на который показываются положение станции, телеметрия и который подставляется как начало ВКД в форме планирования. Все значения в UTC.</p>
      <div class="datetime-fields">
        <label>Дата (UTC)<input type="date" v-model="date" /></label>
        <label>Время (UTC)<input type="time" v-model="time" step="60" /></label>
      </div>
      <p v-if="error" class="form-error">{{ error }}</p>
      <div class="modal-actions">
        <button class="button" type="button" @click="now"><Icon name="Clock3" :size="16" />Сейчас</button>
        <button class="button primary" type="button" @click="apply"><Icon name="Check" :size="16" />Применить</button>
      </div>
    </section>
  </div>
</template>
