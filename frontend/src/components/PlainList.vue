<script setup lang="ts">
import type { PlainSentence } from '../lib/plain';
import Icon from './Icon.vue';
import PlainParts from './PlainParts.vue';

// Пункты объяснения рекомендации после заголовка: предложение о неизмеренном потоке выделено, строки по линиям
// после предложения с двоеточием вложены в него (lib/plain.ts: plainExplanation).
defineProps<{ items: PlainSentence[] }>();
</script>
<template>
  <ul v-if="items.length" class="plain-list">
    <li v-for="(item, i) in items" :key="i" :class="{ caveat: item.caveat }">
      <Icon v-if="item.caveat" name="TriangleAlert" :size="15" />
      <span><PlainParts :parts="item.parts" /></span>
      <ul v-if="item.sub.length" class="plain-sub">
        <li v-for="(sub, j) in item.sub" :key="j"><PlainParts :parts="sub.parts" /></li>
      </ul>
    </li>
  </ul>
</template>
