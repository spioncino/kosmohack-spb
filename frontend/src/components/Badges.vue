<script setup lang="ts">
import type { Availability, Grade, Kind, Level } from '../api/client';
import { availabilityTerm, kindTerm, levelTerm } from '../lib/glossary';
import { AVAILABILITY_LABEL, GRADE_LABEL, KIND_LABEL, LEVEL_LABEL } from '../lib/labels';
import Term from './Term.vue';

// Единый бейдж: kind интервала, уровень, доступность данных или уверенность. Щелчок по бейджу открывает
// пояснение из глоссария со ссылкой на документацию (components/Term.vue).
const props = defineProps<{ kind?: Kind; level?: Level; availability?: Availability; grade?: Grade; small?: boolean }>();

function text(): string {
  if (props.kind) return KIND_LABEL[props.kind];
  if (props.level) return LEVEL_LABEL[props.level];
  if (props.availability) return AVAILABILITY_LABEL[props.availability];
  if (props.grade) return `уверенность ${GRADE_LABEL[props.grade]}`;
  return '';
}

function cls(): string {
  if (props.kind) return `badge-kind badge-kind-${props.kind}`;
  if (props.level) return `badge-level badge-level-${props.level}`;
  if (props.availability) return `badge-avail badge-avail-${props.availability}`;
  if (props.grade) return `badge-grade badge-grade-${props.grade}`;
  return '';
}

function term(): string | null {
  if (props.kind) return kindTerm(props.kind);
  if (props.level) return levelTerm(props.level);
  if (props.availability) return availabilityTerm(props.availability);
  if (props.grade) return 'confidence';
  return null;
}
</script>
<template><Term :term="term()" class="term-badge"><span class="badge" :class="[cls(), { small }]">{{ text() }}</span></Term></template>
