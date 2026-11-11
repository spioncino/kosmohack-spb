<script setup lang="ts">
import { computed, ref } from 'vue';
import { useRouter } from 'vue-router';
import Badges from '../components/Badges.vue';
import Icon from '../components/Icon.vue';
import ProvenanceList from '../components/ProvenanceList.vue';
import { fmtDate, fmtTime, fmtUtc } from '../lib/format';
import { LEVEL_COLOR, LINE_ORDER, MODE_LABEL, lineLabel } from '../lib/labels';
import { periodCaption } from '../lib/period';
import { SHADOW_LINE, filterEvents, groupEventsByDay } from '../lib/score';
import { assessment, events, setSelectedTime } from '../store/state';

const router = useRouter();
const filter = ref('all');
// Тень это условие работ, не событие: интервалы освещённости показываются только по переключателю.
const showShadow = ref(false);
const visible = computed(() => filterEvents(events.value, { showShadow: showShadow.value }));
const filtered = computed(() => filterEvents(events.value, { showShadow: showShadow.value, line: filter.value === 'all' ? undefined : filter.value }));
const groups = computed(() => groupEventsByDay(filtered.value));
const categories = computed(() => LINE_ORDER.filter((line) => visible.value.some((e) => e.line === line)));
const shadowCount = computed(() => events.value.filter((e) => e.line === SHADOW_LINE).length);

// Скрытие тени снимает её фильтр, чтобы список не оставался пустым без объяснения.
function toggleShadow(): void {
  showShadow.value = !showShadow.value;
  if (!showShadow.value && filter.value === SHADOW_LINE) filter.value = 'all';
}
const opened = ref<string | null>(null);

function toOrbit(iso: string): void {
  setSelectedTime(iso);
  router.push('/orbit');
}
</script>
<template>
  <div class="section-toolbar">
    <span class="muted" v-if="assessment">{{ MODE_LABEL[assessment.request.mode] }} · {{ periodCaption(assessment.request) }}</span>
    <span class="muted" v-else>Открытого расчёта нет</span>
    <RouterLink to="/risks" class="button"><Icon name="ShieldCheck" :size="16" />Анализ рисков</RouterLink>
  </div>
  <div class="filter-pills">
    <button type="button" :class="{ active: filter === 'all' }" @click="filter = 'all'">Все ({{ visible.length }})</button>
    <button v-for="line in categories" :key="line" type="button" :class="{ active: filter === line }" @click="filter = line">{{ lineLabel(line) }} ({{ visible.filter((e) => e.line === line).length }})</button>
    <label v-if="shadowCount" class="check shadow-toggle" title="Тень это условие работ, не событие; по умолчанию интервалы освещённости скрыты">
      <input type="checkbox" :checked="showShadow" @change="toggleShadow" />
      <span>показывать тень ({{ shadowCount }})</span>
    </label>
  </div>
  <section class="panel event-timeline" v-if="groups.length">
    <template v-for="group in groups" :key="group.day">
      <div class="event-day">{{ fmtDate(group.day + 'T00:00:00Z') }}</div>
      <div v-for="e in group.events" :key="e.id" class="full-event">
        <time>{{ fmtTime(e.start) }}<small>– {{ fmtTime(e.end) }} UTC</small></time>
        <span class="event-dot" :style="{ color: LEVEL_COLOR[e.level] }"></span>
        <div>
          <span class="eyebrow">{{ e.category }}</span>
          <h2>{{ e.note || e.rule.slice(0, 120) }}</h2>
          <p><Badges :level="e.level" small /> <Badges :kind="e.kind" small /></p>
          <div v-if="opened === e.id" class="event-details">
            <p class="rule-text">{{ e.rule }}</p>
            <ProvenanceList :items="e.interval.provenance" compact />
          </div>
        </div>
        <button class="button" type="button" @click="toOrbit(e.start)">На орбите <Icon name="ArrowUpRight" :size="16" /></button>
        <button class="icon-btn" type="button" :aria-label="opened === e.id ? 'Скрыть источники' : 'Правило и источники'" @click="opened = opened === e.id ? null : e.id"><Icon :name="opened === e.id ? 'ChevronUp' : 'Info'" /></button>
      </div>
    </template>
  </section>
  <div v-else class="panel empty-state">
    <Icon name="Activity" :size="38" />
    <h2>{{ assessment ? 'Интервалов выше фона нет' : 'Нет открытого расчёта' }}</h2>
    <p>{{ assessment ? 'Ни одна линия не дала уровень выше фона на периоде поиска. Линии без данных здесь не показываются: смотрите доступность в анализе рисков.' : 'События собираются из интервалов линий последнего расчёта.' }}<template v-if="assessment && shadowCount && !showShadow"> Интервалы тени ({{ shadowCount }}) скрыты: это условие работ, не событие.</template></p>
    <RouterLink v-if="!assessment" to="/eva" class="button">К планированию</RouterLink>
  </div>
</template>
