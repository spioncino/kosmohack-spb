<script setup lang="ts">
import type { PlainPart } from '../lib/plain';
import Term from './Term.vue';

// Предложение объяснения рекомендации: слова с пояснением в глоссарии открывают его (components/Term.vue),
// остальное показывается текстом (lib/plain.ts: termParts).
defineProps<{ parts: PlainPart[] }>();
</script>
<template><template v-for="(part, i) in parts" :key="i"><Term v-if="part.term" :term="part.term">{{ part.text }}</Term><template v-else>{{ part.text }}</template></template></template>
