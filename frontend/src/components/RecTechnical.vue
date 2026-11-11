<script setup lang="ts">
import { computed } from 'vue';
import type { AssessmentResult } from '../api/client';
import Icon from './Icon.vue';
import { perLineRows, scoreOf } from '../lib/reason';
import LineMinutes from './LineMinutes.vue';
import ReasonView from './ReasonView.vue';
import Segments from './Segments.vue';
import Term from './Term.vue';

// Техническая запись рекомендации: минуты предпочтительного окна по линиям, причина сервера с отрезками и кодами
// решений (components/ReasonView.vue), страховка по геометрии (D61) и число равнозначных окон.
const props = defineProps<{ result: AssessmentResult }>();
const emit = defineEmits<{ orbit: [iso: string] }>();

const preferredRows = computed(() => {
  const rec = props.result.recommendation;
  if (rec.insufficient || !rec.preferred) return [];
  return perLineRows(scoreOf(props.result.windows, rec.preferred.start)?.per_line);
});
</script>
<template>
  <div v-if="preferredRows.length" class="rec-lines">
    <span class="rec-lines-title">Предпочтительное окно по линиям, <Term term="hc_minutes">минуты уровней</Term></span>
    <LineMinutes :rows="preferredRows" />
  </div>
  <ReasonView :reason="result.recommendation.reason" />
  <p v-if="result.geometry" class="rec-geometry"><Icon name="Compass" :size="16" /><span><Segments :text="result.geometry.note" /><template v-if="result.geometry.safest && result.geometry.safest_minutes !== null && result.geometry.safest_minutes < result.geometry.requested_minutes"> <button type="button" class="text-btn" @click="emit('orbit', result.geometry.safest.start)">показать на орбите</button></template></span></p>
  <p v-if="result.recommendation.equivalents.length" class="muted"><Term term="equivalent_windows">Равнозначных окон</Term>: {{ result.recommendation.equivalents.length }} из {{ result.windows.length }} · подробнее в сравнении окон</p>
</template>
