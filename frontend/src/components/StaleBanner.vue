<script setup lang="ts">
import type { StaleNotice } from '../lib/requestForm';
import Icon from './Icon.vue';

// Баннер над показанным результатом, когда он не отвечает форме или идёт новый расчёт (D85, lib/requestForm.ts:
// staleNotice). Кнопка возвращает форму к запросу показанного расчёта и видна, только когда форма изменена.
defineProps<{ notice: StaleNotice; canRevert?: boolean }>();
defineEmits<{ revert: [] }>();
</script>
<template>
  <section class="stale-banner" :class="{ running: notice.running }" role="status" aria-live="polite">
    <Icon :name="notice.running ? 'LoaderCircle' : 'TriangleAlert'" :size="22" :class="{ spinning: notice.running }" />
    <div class="stale-body">
      <strong>{{ notice.title }}</strong>
      <p v-if="notice.detail" class="stale-detail">{{ notice.detail }}</p>
      <p class="stale-hint">{{ notice.hint }}</p>
    </div>
    <button v-if="canRevert" class="button" type="button" @click="$emit('revert')"><Icon name="History" :size="16" />Вернуть параметры расчёта</button>
  </section>
</template>
