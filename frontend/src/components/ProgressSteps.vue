<script setup lang="ts">
import { computed } from 'vue';
import type { Job } from '../api/client';
import { jobErrorText } from '../lib/currentMode';
import { ago } from '../lib/format';
import { PROGRESS_STEPS, STEP_LABEL } from '../lib/labels';
import { clock, health, job as lastJob } from '../store/state';
import Icon from './Icon.vue';

const props = defineProps<{ job: Job | null; error?: string }>();

const fraction = computed(() => props.job?.progress?.fraction ?? 0);
const steps = computed(() =>
  PROGRESS_STEPS.map((s) => ({
    ...s,
    label: STEP_LABEL[s.step] || s.step,
    state: props.job?.status === 'done' ? 'done' : fraction.value >= s.fraction ? 'done' : props.job?.progress?.step === s.step ? 'current' : 'pending',
  })),
);
// Ошибка задания без эфемериды МКС показывается обычными словами (D87), исходный текст остаётся в подсказке. Режим
// берётся из последнего задания хранилища: ошибку ставит оно же, а /compare передаёт job только пока оно идёт.
const errorText = computed(() => (props.error ? jobErrorText(props.error, lastJob.value?.params?.mode) : ''));
const workerNote = computed(() => (health.value?.worker_last_tick ? `воркер: тик ${ago(health.value.worker_last_tick, clock.value)}` : 'воркер не запускался: задание останется в очереди'));
</script>
<template>
  <section class="panel progress-panel" v-if="job || error">
    <div class="card-header">
      <h2>
        <Icon v-if="job && (job.status === 'queued' || job.status === 'running')" name="LoaderCircle" class="spinning" :size="18" />
        <template v-if="!job">Задание не поставлено</template>
        <template v-else-if="job.status === 'queued'">Задание {{ job.id }} в очереди</template>
        <template v-else-if="job.status === 'running'">Задание {{ job.id }}: {{ STEP_LABEL[job.progress?.step || ''] || job.progress?.step || 'выполняется' }} · {{ Math.round(fraction * 100) }} %</template>
        <template v-else-if="job.status === 'done'">Задание {{ job.id }} выполнено</template>
        <template v-else>Задание {{ job.id }} не выполнено</template>
      </h2>
      <span class="muted" v-if="job?.status === 'queued'">{{ workerNote }}</span>
    </div>
    <div class="progress-bar"><i :style="{ width: `${Math.round(fraction * 100)}%` }" /></div>
    <ol class="progress-steps" v-if="job">
      <li v-for="s in steps" :key="s.step" :class="s.state">
        <Icon :name="s.state === 'done' ? 'CheckCircle2' : s.state === 'current' ? 'LoaderCircle' : 'Circle'" :size="14" :class="{ spinning: s.state === 'current' }" />
        <span>{{ s.label }}</span>
        <small>{{ Math.round(s.fraction * 100) }} %</small>
      </li>
    </ol>
    <p v-if="error" class="form-error" :title="errorText !== error ? error : undefined"><Icon name="TriangleAlert" :size="16" />{{ errorText }}</p>
  </section>
</template>
