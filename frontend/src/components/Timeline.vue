<script setup lang="ts">
import { computed } from 'vue';
import type { AssessmentResult, Interval, Level, TrajectorySample } from '../api/client';
import { fmtTime, fmtUtc, ms } from '../lib/format';
import { KIND_LABEL, LEVEL_COLOR, LEVEL_LABEL, LINE_ORDER, lineBadge, lineLabel } from '../lib/labels';
import { SAA_LINE, saaPassInterval } from '../lib/saa';
import Badges from './Badges.vue';

// variant «main»: все линии по данным до момента данных, без строки факта; «verification»: только проверяемая
// линия, её прогноз, факт и окно по факту. Факт это не независимые наблюдения, а тот же расчёт по всем данным, доступным сегодня, включая опубликованные позже отсечения.
const FACT_TEXT = 'тот же расчёт по всем данным, доступным сегодня, включая опубликованные позже отсечения';
const props = withDefaults(defineProps<{ result: AssessmentResult; variant?: 'main' | 'verification' }>(), { variant: 'main' });
const emit = defineEmits<{ select: [interval: Interval, line: string] }>();

const start = computed(() => ms(props.result.request.search_start));
const end = computed(() => ms(props.result.request.search_end));
const span = computed(() => Math.max(1, end.value - start.value));

const pct = (t: number) => `${((Math.min(Math.max(t, start.value), end.value) - start.value) / span.value) * 100}%`;
const width = (a: number, b: number) => `${((Math.min(b, end.value) - Math.max(a, start.value)) / span.value) * 100}%`;

const ticks = computed(() => {
  const hours = span.value / 3600000;
  const stepH = hours <= 6 ? 1 : hours <= 14 ? 2 : hours <= 30 ? 3 : 6;
  const first = Math.ceil(start.value / (stepH * 3600000)) * stepH * 3600000;
  const list: number[] = [];
  for (let t = first; t <= end.value; t += stepH * 3600000) list.push(t);
  return list;
});

const lines = computed(() => {
  const byName = new Map(props.result.lines.map((line) => [line.line, line]));
  if (props.variant === 'verification') {
    const verified = props.result.verification ? byName.get(props.result.verification.line) : undefined;
    return verified ? [verified] : [];
  }
  const names = [...LINE_ORDER.filter((n) => byName.has(n)), ...props.result.lines.map((l) => l.line).filter((n) => !LINE_ORDER.includes(n as never))];
  return names.map((name) => byName.get(name)!);
});
const showFact = computed(() => props.variant === 'verification' && !!props.result.verification);
const factWindow = computed(() => (props.variant === 'verification' ? props.result.verification?.observed_preferred ?? null : null));
const conditionalHigh = computed(() => props.result.lines.some((l) => l.intervals.some((iv) => iv.level === 'high' && iv.kind === 'external_forecast')));

// Отрезки ЮАА по сэмплам траектории: подряд идущие точки с in_saa.
const saaRuns = computed(() => {
  const runs: { a: number; b: number }[] = [];
  let current: { a: number; b: number } | null = null;
  const samples: TrajectorySample[] = props.result.trajectory.samples;
  for (let i = 0; i < samples.length; i++) {
    const t = ms(samples[i].t);
    if (samples[i].in_saa) {
      if (current) current.b = t;
      else current = { a: t, b: t };
    } else if (current) {
      runs.push(current);
      current = null;
    }
  }
  if (current) runs.push(current);
  return runs.filter((r) => r.b > start.value && r.a < end.value);
});

const saaPasses = computed(() => props.result.trajectory.saa_passes ?? null);
const requested = computed(() => props.result.requested_window.window);
const preferred = computed(() => props.result.recommendation.preferred);
const verification = computed(() => props.result.verification);

function title(interval: Interval): string {
  return `${LEVEL_LABEL[interval.level]} · ${KIND_LABEL[interval.kind]}\n${fmtUtc(interval.start)} – ${fmtUtc(interval.end)}\n${interval.note || interval.rule}`;
}

const color = (level: Level) => LEVEL_COLOR[level];
</script>
<template>
  <div class="tl">
    <div class="tl-axis">
      <span v-for="t in ticks" :key="t" :style="{ left: pct(t) }">{{ fmtTime(t) }}</span>
    </div>
    <div class="tl-rows">
      <div v-for="line in lines" :key="line.line" class="tl-row">
        <span class="tl-label">
          <span>{{ lineLabel(line.line) }}{{ variant === 'verification' ? ': прогноз до отсечения' : '' }}</span>
          <Badges v-bind="lineBadge(line)" small />
        </span>
        <div class="tl-track">
          <button
            v-for="(iv, i) in line.intervals"
            :key="i"
            type="button"
            class="tl-bar"
            :class="[`kind-${iv.kind}`, `level-${iv.level}`]"
            :style="{ left: pct(ms(iv.start)), width: width(ms(iv.start), ms(iv.end)), background: color(iv.level) }"
            :title="title(iv)"
            :aria-label="title(iv)"
            @click="emit('select', iv, line.line)"
          ></button>
          <span v-if="!line.intervals.length" class="tl-empty">{{ line.availability === 'available' ? 'интервалов нет' : 'нет данных' }}</span>
        </div>
      </div>
      <div v-if="showFact && verification" class="tl-row">
        <span class="tl-label" :title="`факт: ${FACT_TEXT}; не независимые наблюдения; в рекомендацию не входит`"><span>{{ lineLabel(verification.line) }}: факт после события</span><Badges :availability="verification.observed_availability" small /></span>
        <div class="tl-track">
          <button
            v-for="(iv, i) in verification.observed"
            :key="i"
            type="button"
            class="tl-bar observed"
            :style="{ left: pct(ms(iv.start)), width: width(ms(iv.start), ms(iv.end)), background: color(iv.level) }"
            :title="`факт: ${title(iv)}`"
            @click="emit('select', iv, verification.line)"
          ></button>
        </div>
      </div>
      <div v-if="variant === 'main'" class="tl-row">
        <span class="tl-label"><span>ЮАА по трассе</span><small>наш расчёт</small></span>
        <div class="tl-track">
          <template v-if="saaPasses">
            <button
              v-for="(pass, i) in saaPasses"
              :key="i"
              type="button"
              class="tl-saa"
              :style="{ left: pct(ms(pass.start)), width: width(ms(pass.start), ms(pass.end)) }"
              :title="`ЮАА ${fmtTime(ms(pass.start))}–${fmtTime(ms(pass.end))} UTC · минимум |B| ${Math.round(pass.min_b_nt)} нТл при пороге ${Math.round(pass.threshold_nt)} нТл`"
              :aria-label="`ЮАА ${fmtTime(ms(pass.start))}–${fmtTime(ms(pass.end))} UTC`"
              @click="emit('select', saaPassInterval(pass), SAA_LINE)"
            ></button>
          </template>
          <i v-for="(run, i) in saaRuns" v-else :key="`run-${i}`" class="tl-saa" :style="{ left: pct(run.a), width: width(run.a, run.b + 60000) }" :title="`ЮАА ${fmtTime(run.a)}–${fmtTime(run.b)} UTC`"></i>
        </div>
      </div>
      <div class="tl-overlays">
        <div class="tl-window requested" :style="{ left: pct(ms(requested.start)), width: width(ms(requested.start), ms(requested.end)) }" :title="`запрошенное окно ${fmtUtc(requested.start)} – ${fmtUtc(requested.end)}`"><span>запрошено</span></div>
        <div v-if="preferred" class="tl-window preferred" :style="{ left: pct(ms(preferred.start)), width: width(ms(preferred.start), ms(preferred.end)) }" :title="`предпочтительное окно ${fmtUtc(preferred.start)} – ${fmtUtc(preferred.end)}`"><span>предпочтительное</span></div>
        <div v-if="factWindow" class="tl-window fact" :style="{ left: pct(ms(factWindow.start)), width: width(ms(factWindow.start), ms(factWindow.end)) }" :title="`окно по факту ${fmtUtc(factWindow.start)} – ${fmtUtc(factWindow.end)}: лучшее окно того же расчёта по всем данным, доступным сегодня`"><span>по факту</span></div>
      </div>
    </div>
    <div class="legend tl-legend">
      <span v-for="level in (['none', 'elevated', 'high', 'critical', 'unknown'] as Level[])" :key="level"><i :style="{ background: color(level) }" />{{ LEVEL_LABEL[level] }}</span>
      <span><i class="legend-kind kind-observation" />наблюдение</span>
      <span><i class="legend-kind kind-external_forecast" />внешний прогноз</span>
      <span><i class="legend-kind kind-computed" />наш расчёт</span>
      <span v-if="conditionalHigh"><i class="legend-cond" />высокий с пунктиром: виток открыт для протонов при реализации прогноза, поток не измерен (D47)</span>
      <span v-if="showFact"><i class="legend-fact" />факт после события: {{ FACT_TEXT }}</span>
      <span v-if="variant === 'main'"><i class="legend-saa" />проход через ЮАА</span>
    </div>
  </div>
</template>
