<script setup lang="ts">
import type { Provenance } from '../api/client';
import { fmtUtc } from '../lib/format';
import { openEvidence } from '../store/state';
import Icon from './Icon.vue';

defineProps<{ items: Provenance[]; compact?: boolean }>();
</script>
<template>
  <ul class="provenance-list" :class="{ compact }">
    <li v-for="p in items" :key="p.raw_id + p.product">
      <button class="evidence-link" type="button" @click="openEvidence(p.raw_id)">
        <Icon name="Database" :size="13" />
        <span>{{ p.source }}/{{ p.product }}</span>
      </button>
      <small>версия {{ p.data_version }} · доступно с {{ p.published_at ? fmtUtc(p.published_at) : 'неизвестно' }} ({{ p.published_at_kind }})</small>
    </li>
  </ul>
</template>
