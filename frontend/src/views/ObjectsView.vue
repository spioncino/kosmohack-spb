<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { api, errorText, type ConjunctionsLatest } from '../api/client';
import Badges from '../components/Badges.vue';
import Icon from '../components/Icon.vue';
import ProvenanceList from '../components/ProvenanceList.vue';
import {
  HISTORICAL_TEXT,
  LEVEL_HINT,
  NO_REPORT_NOTE,
  NO_REPORT_TEXT,
  emptyText,
  fmtMaxprob,
  intervalText,
  isHistorical,
  isPast,
  isStale,
  reportState,
  reportTimeText,
  sortByTca,
  staleHint,
} from '../lib/conjunctions';
import { fmtUtc, num } from '../lib/format';
import { latestGuard } from '../lib/latest';
import { MODE_LABEL } from '../lib/labels';
import { periodMinutes } from '../lib/orbit';
import { periodCaption } from '../lib/period';
import { assessment, clock, orbit, orbitError, setSelectedTime, station } from '../store/state';

const router = useRouter();
const line = computed(() => assessment.value?.lines.find((l) => l.line === 'conjunctions') || null);
const historical = computed(() => (assessment.value ? isHistorical(assessment.value.request.mode) : false));

// Текущий отчёт SOCRATES не зависит от открытого расчёта: он виден и при открытом разборе прошлых дат.
const report = ref<ConjunctionsLatest | null>(null);
const reportError = ref('');
const reportLoading = ref(false);
const reportGuard = latestGuard();

async function loadReport(): Promise<void> {
  const request = reportGuard.begin();
  reportLoading.value = true;
  try {
    const value = await api.conjunctionsLatest();
    if (!reportGuard.isCurrent(request)) return;
    report.value = value;
    reportError.value = '';
  } catch (error) {
    if (reportGuard.isCurrent(request)) reportError.value = errorText(error);
  } finally {
    if (reportGuard.isCurrent(request)) reportLoading.value = false;
  }
}

onMounted(loadReport);

const state = computed(() => (report.value ? reportState(report.value) : null));
const rows = computed(() => (report.value ? sortByTca(report.value.conjunctions) : []));
const stale = computed(() => (report.value ? isStale(report.value, clock.value) : false));

interface Conjunction {
  name: string;
  catnr: string;
  tca: string;
  rangeKm: number;
  speedKms: number;
  maxprob: number;
  level: string;
  kind: string;
  intervalStart: string;
  intervalEnd: string;
  provenance: (typeof assessment.value extends null ? never : NonNullable<typeof assessment.value>)['sources_used'];
}

// Правило интервала содержит сближения в фиксированном формате conjunction_rule (risk/lines/conjunctions.py).
const PATTERN = /([^;]+?) \((\d+)\): TCA (\S+), минимальная дистанция ([\d.]+) км, относительная скорость ([\d.]+) км\/с, MAXPROB ([\d.eE+-]+)/g;

const conjunctions = computed<Conjunction[]>(() => {
  if (!line.value) return [];
  const seen = new Map<string, Conjunction>();
  for (const iv of line.value.intervals) {
    for (const m of iv.rule.matchAll(PATTERN)) {
      const key = `${m[2]}@${m[3]}`;
      if (seen.has(key)) continue;
      seen.set(key, { name: m[1].trim(), catnr: m[2], tca: m[3], rangeKm: Number(m[4]), speedKms: Number(m[5]), maxprob: Number(m[6]), level: iv.level, kind: iv.kind, intervalStart: iv.start, intervalEnd: iv.end, provenance: iv.provenance });
    }
  }
  return [...seen.values()].sort((a, b) => a.tca.localeCompare(b.tca));
});

const omm = computed(() => orbit.value?.omm || null);

function toOrbit(iso: string): void {
  setSelectedTime(iso);
  router.push('/orbit');
}
</script>
<template>
  <div class="section-toolbar">
    <span class="muted" v-if="assessment">{{ MODE_LABEL[assessment.request.mode] }} · {{ periodCaption(assessment.request) }}</span>
    <span class="muted" v-else>Открытого расчёта нет</span>
    <RouterLink to="/orbit" class="button"><Icon name="Orbit" :size="18" />Открыть орбиту</RouterLink>
  </div>

  <section class="panel table-panel">
    <table>
      <thead><tr><th>Объект</th><th>NORAD</th><th>Эпоха элементов</th><th>Наклонение</th><th>Период</th><th>Высота сейчас</th><th>Источник</th></tr></thead>
      <tbody>
        <tr>
          <td><Icon name="Satellite" />{{ omm?.OBJECT_NAME || 'ISS (ZARYA)' }}</td>
          <td class="mono">{{ omm?.NORAD_CAT_ID || '25544' }}</td>
          <td class="mono">{{ omm ? fmtUtc(omm.EPOCH) : '—' }}</td>
          <td class="mono">{{ omm ? `${num(Number(omm.INCLINATION), 3)}°` : '—' }}</td>
          <td class="mono">{{ omm ? `${num(periodMinutes(omm), 1)} мин` : '—' }}</td>
          <td class="mono">{{ station.point ? `${num(station.point.alt, 1)} км` : '—' }}</td>
          <td>
            <ProvenanceList v-if="orbit?.provenance" :items="[orbit.provenance]" compact />
            <span v-else class="muted">{{ orbitError ? `элементы недоступны: ${orbitError}` : 'элементы не загружены' }}</span>
          </td>
        </tr>
      </tbody>
    </table>
  </section>

  <section class="panel table-panel current-report" aria-labelledby="current-conjunctions">
    <div class="card-header">
      <h2 id="current-conjunctions">Текущие сближения МКС (CelesTrak SOCRATES)</h2>
      <div class="badge-row">
        <span v-if="stale && report" class="badge badge-avail-stale" :title="staleHint(report)">отчёт устарел</span>
        <Badges v-if="report?.available" kind="external_forecast" />
        <Badges v-else-if="report" availability="missing" />
        <button class="text-btn" type="button" :disabled="reportLoading" @click="loadReport"><Icon name="RefreshCw" :size="13" />{{ reportLoading ? 'Загрузка…' : 'Обновить' }}</button>
      </div>
    </div>

    <div v-if="report?.available" class="report-meta">
      <span class="mono">{{ reportTimeText(report, clock) }}</span>
      <span v-if="intervalText(report, clock)" class="mono">{{ intervalText(report, clock) }}</span>
      <span v-if="stale" class="stale-note">{{ staleHint(report) }}</span>
    </div>

    <div v-if="!report" class="empty-state small">
      <Icon name="Radar" :size="30" />
      <p v-if="reportError">Отчёт SOCRATES не получен: {{ reportError }}. {{ NO_REPORT_NOTE }}</p>
      <p v-else>Загрузка последнего отчёта SOCRATES…</p>
    </div>
    <div v-else-if="state === 'missing'" class="empty-state small report-missing">
      <Icon name="Radar" :size="30" />
      <p><strong>{{ NO_REPORT_TEXT }}</strong></p>
      <p>{{ NO_REPORT_NOTE }}</p>
    </div>
    <div v-else-if="state === 'empty'" class="empty-state small">
      <Icon name="Radar" :size="30" />
      <p>{{ emptyText(report) }}</p>
    </div>
    <table v-else>
      <thead><tr><th>TCA (UTC)</th><th>Объект</th><th>NORAD</th><th>Мин. дистанция</th><th>Отн. скорость</th><th>MAXPROB <small>верхняя оценка</small></th><th>Уровень</th></tr></thead>
      <tbody>
        <tr v-for="c in rows" :key="c.secondary_catnr + c.tca" :class="{ past: isPast(c.tca, clock) }">
          <td class="mono">{{ fmtUtc(c.tca, true) }}<small v-if="isPast(c.tca, clock)" class="past-label">прошло</small></td>
          <td><span class="object-name"><Icon name="Crosshair" :size="16" />{{ c.secondary_name }}</span></td>
          <td class="mono">{{ c.secondary_catnr }}</td>
          <td class="mono">{{ num(c.min_range_km, 3) }} км</td>
          <td class="mono">{{ num(c.rel_speed_kms, 3) }} км/с</td>
          <td class="mono" title="MAXPROB SOCRATES это верхняя оценка по публичным элементам, не Pc (D30, D53)">{{ fmtMaxprob(c.max_probability) }}</td>
          <td :title="LEVEL_HINT[c.level]"><Badges :level="c.level" small /></td>
        </tr>
      </tbody>
    </table>

    <div v-if="report?.provenance" class="panel-note">
      <ProvenanceList :items="[report.provenance]" compact />
    </div>
  </section>

  <section class="panel table-panel" v-if="line">
    <div class="card-header">
      <h2>Сближения в открытом расчёте</h2>
      <div class="badge-row"><Badges :availability="line.availability" /><Badges :grade="line.confidence.grade" /></div>
    </div>
    <div v-if="historical" class="empty-state small">
      <Icon name="Radar" :size="30" />
      <p class="historical-text">{{ HISTORICAL_TEXT }}</p>
    </div>
    <table v-else-if="conjunctions.length">
      <thead><tr><th>Объект</th><th>NORAD</th><th>TCA (UTC)</th><th>Мин. дистанция</th><th>Отн. скорость</th><th>MAXPROB <small>верхняя оценка</small></th><th>Уровень</th><th>Интервал планирования</th><th></th></tr></thead>
      <tbody>
        <tr v-for="c in conjunctions" :key="c.catnr + c.tca">
          <td><Icon name="Crosshair" :size="16" />{{ c.name }}</td>
          <td class="mono">{{ c.catnr }}</td>
          <td class="mono">{{ fmtUtc(c.tca, true) }}</td>
          <td class="mono">{{ num(c.rangeKm, 3) }} км</td>
          <td class="mono">{{ num(c.speedKms, 3) }} км/с</td>
          <td class="mono" title="MAXPROB SOCRATES это верхняя оценка по публичным элементам, не Pc (D30)">{{ fmtMaxprob(c.maxprob) }}</td>
          <td><Badges :level="c.level as never" small /> <Badges :kind="c.kind as never" small /></td>
          <td class="mono">{{ fmtUtc(c.intervalStart) }} – {{ fmtUtc(c.intervalEnd) }}</td>
          <td><button class="text-btn" type="button" @click="toOrbit(c.tca)">На орбите <Icon name="ArrowUpRight" :size="13" /></button></td>
        </tr>
      </tbody>
    </table>
    <div v-else class="empty-state small">
      <Icon name="Radar" :size="30" />
      <p v-if="line.availability !== 'available'">{{ line.availability === 'unusable_for_replay' ? HISTORICAL_TEXT : 'Данных о сближениях нет. Отсутствие отчёта не означает отсутствие сближений.' }}</p>
      <p v-else>В периоде поиска открытого расчёта: {{ line.intervals[0]?.note || 'сближений ближе порога расчёта не найдено' }}.</p>
    </div>
    <div class="panel-note">
      <p class="muted">{{ line.confidence.depends_on }}</p>
      <ul class="limits"><li v-for="l in line.limitations" :key="l">{{ l }}</li></ul>
    </div>
  </section>
  <div v-else class="panel info-banner"><Icon name="Info" /><p>{{ historical ? HISTORICAL_TEXT : 'Интервалы линии сближений появятся после расчёта в режиме «сейчас»; текущий отчёт SOCRATES показан выше.' }}</p></div>
</template>

<style scoped>
.report-meta{display:flex;flex-wrap:wrap;gap:6px 18px;padding:0 16px 12px;font-size:12px;color:#aab6bd}
.report-meta .stale-note{color:var(--yellow)}
.report-missing strong{color:#a2aab1;font-weight:550}
.current-report .badge-row{margin:0}
.current-report .text-btn{gap:6px}
.current-report tr.past td{color:#7d878f}
.past-label{margin-left:8px;font-size:10px}
.object-name{display:inline-flex;align-items:center;gap:8px}
.historical-text{max-width:640px;color:#aab6bd}
.current-report .panel-note .provenance-list{margin-top:0}
</style>
