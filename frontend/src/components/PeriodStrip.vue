<script setup lang="ts">
import { computed, ref } from 'vue';
import { fmtDate, fmtTime, fmtWindow, isoZ } from '../lib/format';
import { LEVEL_COLOR } from '../lib/labels';
import { fraction, hourTicks, type LevelRun, type Span } from '../lib/scope';

// Шкала периода карты: уровни линии протонов вдоль времени, запрошенное и рекомендованное окна, курсор времени.
const props = defineProps<{
  range: Span;
  runs: LevelRun[];
  requested?: Span | null;
  preferred?: Span | null;
  // Период допустимого начала окон: от начала периода поиска до его конца минус длительность ВКД.
  starts?: Span | null;
  selected: number;
  leveled: boolean;
}>();
const emit = defineEmits<{ pick: [time: number] }>();

const bar = ref<HTMLElement>();
const pct = (t: number) => `${fraction(t, props.range) * 100}%`;
const width = (s: Span) => `${(fraction(s.end, props.range) - fraction(s.start, props.range)) * 100}%`;
const ticks = computed(() => hourTicks(props.range));
const cursorInside = computed(() => props.selected >= props.range.start && props.selected <= props.range.end);
const startsText = computed(() => (props.starts ? `допустимое начало ${fmtWindow({ start: isoZ(props.starts.start), end: isoZ(props.starts.end) })}` : ''));
const sameWindow = computed(() => !!props.requested && !!props.preferred && props.requested.start === props.preferred.start && props.requested.end === props.preferred.end);

let dragging = false;
function pickAt(clientX: number): void {
  const rect = bar.value!.getBoundingClientRect();
  const share = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
  const t = props.range.start + share * (props.range.end - props.range.start);
  emit('pick', Math.round(t / 60000) * 60000);
}
function down(e: PointerEvent): void {
  dragging = true;
  (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  pickAt(e.clientX);
}
function move(e: PointerEvent): void {
  if (dragging) pickAt(e.clientX);
}
function up(): void {
  dragging = false;
}
</script>
<template>
  <div class="period-strip">
    <div class="ps-bar" ref="bar" role="slider" aria-label="Время на трассе" :aria-valuemin="range.start" :aria-valuemax="range.end" :aria-valuenow="selected" @pointerdown="down" @pointermove="move" @pointerup="up" @pointercancel="up">
      <div class="ps-levels">
        <i v-for="(r, i) in runs" :key="i" :style="{ left: pct(r.start), width: width(r), background: leveled ? LEVEL_COLOR[r.level] : '#3d4a52' }"></i>
      </div>
      <div v-if="starts" class="ps-starts" :style="{ left: pct(starts.start), width: width(starts) }" :title="startsText"></div>
      <div v-if="requested" class="ps-window requested" :style="{ left: pct(requested.start), width: width(requested) }"><span>{{ sameWindow ? 'запрошено и рекомендовано' : 'запрошено' }} {{ fmtTime(requested.start) }}–{{ fmtTime(requested.end) }}</span></div>
      <div v-if="preferred && !sameWindow" class="ps-window preferred" :style="{ left: pct(preferred.start), width: width(preferred) }"><span>рекомендовано {{ fmtTime(preferred.start) }}–{{ fmtTime(preferred.end) }}</span></div>
      <div v-if="cursorInside" class="ps-cursor" :style="{ left: pct(selected) }"><b>{{ fmtTime(selected) }}</b></div>
    </div>
    <div class="ps-ticks"><span v-for="t in ticks" :key="t" :style="{ left: pct(t) }">{{ fmtTime(t) }}</span></div>
    <div class="ps-ends"><span>начало · {{ fmtDate(range.start) }} {{ fmtTime(range.start) }} UTC</span><span v-if="starts" class="ps-starts-text">{{ startsText }}</span><span>конец · {{ fmtDate(range.end) }} {{ fmtTime(range.end) }} UTC</span></div>
  </div>
</template>
