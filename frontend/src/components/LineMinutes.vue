<script setup lang="ts">
import type { LineMinutesRow } from '../lib/reason';

// Строки окна по линиям: «протоны: high+critical 72 мин, elevated 288 мин, без уровня 0 мин» (lib/reason.ts: perLineRows).
// plain: те же числа обычными словами «опасные, повышенный фон, без данных».
defineProps<{ rows: LineMinutesRow[]; plain?: boolean }>();
</script>
<template>
  <ul class="line-minutes">
    <li v-for="row in rows" :key="row.line" :aria-label="plain ? row.plainText : row.text">
      <span class="line-minutes-name">{{ row.word }}:</span>
      <span :class="{ red: row.highCritical > 0 }">{{ plain ? 'опасные' : 'high+critical' }} <b class="mono">{{ row.highCritical }}</b> мин,</span>
      <span :class="{ yellow: row.elevated > 0 }">{{ plain ? 'повышенный фон' : 'elevated' }} <b class="mono">{{ row.elevated }}</b> мин,</span>
      <span :class="{ yellow: row.unknown > 0 }">{{ plain ? 'без данных' : 'без уровня' }} <b class="mono">{{ row.unknown }}</b> мин</span>
    </li>
  </ul>
</template>
