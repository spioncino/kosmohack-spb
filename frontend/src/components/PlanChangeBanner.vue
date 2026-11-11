<script setup lang="ts">
import { computed } from 'vue';
import { planChangeBanner } from '../lib/plan';
import { assessment, dismissPlanChange, openAssessment, planChange } from '../store/state';
import Icon from './Icon.vue';

// Баннер «изменение плана» у расчёта, полученного сменой длительности ВКД; у прежнего расчёта не показывается.
const change = computed(() => (planChange.value && assessment.value?.id === planChange.value.to.id ? planChange.value : null));
const banner = computed(() => (change.value ? planChangeBanner(change.value) : null));
</script>
<template>
  <section v-if="change && banner" class="plan-change" role="status">
    <Icon name="TriangleAlert" :size="16" />
    <div class="plan-change-body">
      <strong>{{ banner.title }}</strong>
      <dl class="plan-change-rows">
        <div v-for="row in banner.rows" :key="row.label"><dt>{{ row.label }}</dt><dd>{{ row.text }}</dd></div>
      </dl>
      <p>{{ banner.note }}</p>
      <button class="text-btn" type="button" @click="openAssessment(change.from.id)">Открыть прежний расчёт {{ change.from.id }} <Icon name="ArrowUpRight" :size="13" /></button>
    </div>
    <button class="icon-btn" type="button" aria-label="Скрыть" title="Скрыть" @click="dismissPlanChange"><Icon name="X" :size="15" /></button>
  </section>
</template>
