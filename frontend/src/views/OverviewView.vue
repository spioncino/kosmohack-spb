<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import Badges from '../components/Badges.vue';
import Icon from '../components/Icon.vue';
import OrbitMap2D from '../components/OrbitMap2D.vue';
import OrbitScene from '../components/OrbitScene.vue';
import PeriodStrip from '../components/PeriodStrip.vue';
import PlainList from '../components/PlainList.vue';
import PlainParts from '../components/PlainParts.vue';
import Station from '../components/Station.vue';
import { ago, fmtDate, fmtTime, fmtUtc, ms, num } from '../lib/format';
import { LEVEL_COLOR, LEVEL_LABEL, MODE_LABEL, sourceLabel } from '../lib/labels';
import { periodMinutes } from '../lib/orbit';
import { dangerMinutes, dangerPasses, levelRuns, periodOf, samplesIn, startsOf, windowsOf, type FocusKind, type Span } from '../lib/scope';
import { periodCaption } from '../lib/period';
import { plainBrief } from '../lib/plain';
import { BADGE_RULE, filterEvents, unmeasuredLabel, upcomingEvents, windowLabel } from '../lib/score';
import { assessment, clock, events, orbit, orbitError, scores, selectedTime, setSelectedTime, sources, sourcesError, station, track, trajectoryError, trajectoryLoading } from '../store/state';

const route = useRoute();
const router = useRouter();
const isOrbit = computed(() => route.path === '/orbit');

// Режим сцены переключается сегментом; ссылка вида #/orbit?view=2d открывает карту сразу.
const mode = ref<'3D' | '2D'>(route.query.view === '2d' ? '2D' : '3D');
const webglUnsupported = ref(false);
const layers = ref<Record<string, boolean>>({ iss: true, orbit: true, countries: true, grid: true, shadow: true, saa: true, labels: true, follow: true });
const allLayers: [string, string][] = [['iss', 'МКС'], ['orbit', 'Трасса'], ['shadow', 'Тень'], ['saa', 'ЮАА'], ['countries', 'Материки'], ['grid', 'Сетка'], ['labels', 'Подписи'], ['follow', 'Следить за МКС']];

const sceneCard = ref<HTMLElement>();
const fullscreen = ref(false);
function onFullscreenChange(): void {
  fullscreen.value = !!document.fullscreenElement;
}
function toggleFullscreen(): void {
  if (document.fullscreenElement) document.exitFullscreen?.();
  else sceneCard.value?.requestFullscreen?.();
}

// Слежение камеры есть только у глобуса: на плоской карте вся Земля видна сразу.
const layerNames = computed(() => allLayers.filter(([key]) => key !== 'follow' || (mode.value === '3D' && !webglUnsupported.value)));

const position = computed(() => (station.value.point ? { lat: station.value.point.lat, lon: station.value.point.lon, alt: station.value.point.alt } : null));
const selectedMs = computed(() => selectedTime.value.getTime());

// Карта показывает либо период открытого расчёта, либо трассу вокруг текущего момента: это видно в подписи.
const inAssessment = computed(() => !!assessment.value && track.value.source === 'assessment');
const windows = computed(() => (inAssessment.value ? windowsOf(assessment.value!) : null));
const starts = computed(() => (inAssessment.value ? startsOf(assessment.value!) : null));
const focusKind = ref<FocusKind>('requested');
watch(
  () => assessment.value?.id,
  () => (focusKind.value = windows.value?.moved ? 'preferred' : 'requested'),
  { immediate: true },
);
// Эфемерида расчёта берётся с запасом в час по краям; карта и шкала показывают ровно период поиска.
const samples = computed(() => (inAssessment.value ? samplesIn(track.value.samples, periodOf(assessment.value!)) : track.value.samples));
const runs = computed(() => levelRuns(samples.value));
const passes = computed(() => (inAssessment.value ? dangerPasses(samples.value) : []));
const requestedMinutes = computed(() => (windows.value ? dangerMinutes(runs.value, windows.value.requested) : 0));
const preferredMinutes = computed(() => (windows.value?.preferred ? dangerMinutes(runs.value, windows.value.preferred) : 0));
function toAssessmentPeriod(): void {
  if (assessment.value) setSelectedTime(assessment.value.requested_window.window.start);
  playing.value = false;
}
function toNow(): void {
  setSelectedTime(Date.now());
  playing.value = false;
}

const trackRange = computed(() => {
  const s = samples.value;
  return s.length ? { start: ms(s[0].t), end: ms(s[s.length - 1].t) } : null;
});
const focusSpan = computed<Span | null>(() => {
  if (!windows.value || !trackRange.value) return null;
  if (focusKind.value === 'preferred' && windows.value.preferred) return windows.value.preferred;
  if (focusKind.value === 'period') return trackRange.value;
  return windows.value.requested;
});
const passInFocus = (p: Span) => !focusSpan.value || (p.end >= focusSpan.value.start && p.start <= focusSpan.value.end);
const periodHours = computed(() => (trackRange.value ? Math.round((trackRange.value.end - trackRange.value.start) / 360000) / 10 : 0));
const playing = ref(false);
const speed = ref(60);
let timer: ReturnType<typeof setInterval> | undefined;
onMounted(() => {
  document.addEventListener('fullscreenchange', onFullscreenChange);
  timer = setInterval(() => {
    if (playing.value) {
      const next = selectedMs.value + speed.value * 1000;
      if (trackRange.value && next > trackRange.value.end) playing.value = false;
      else setSelectedTime(next);
    }
  }, 1000);
});
onUnmounted(() => {
  document.removeEventListener('fullscreenchange', onFullscreenChange);
  clearInterval(timer);
});

const trackStatus = computed(() => {
  if (track.value.source === 'none') return { dot: 'muted', text: trajectoryLoading.value ? 'ТРАССА ЗАГРУЖАЕТСЯ' : `ТРАССА НЕДОСТУПНА${trajectoryError.value ? ` · ${trajectoryError.value}` : ''}` };
  if (track.value.source === 'sgp4') return { dot: 'yellow', text: `SGP4 ПО OMM · /trajectory недоступен${trajectoryError.value ? ` (${trajectoryError.value})` : ''}` };
  return { dot: 'teal', text: track.value.label.toUpperCase() };
});

const omm = computed(() => orbit.value?.omm || null);
const availableLines = computed(() => scores.value.filter((s) => s.percent !== null).length);
const thresholdsHint = BADGE_RULE;

// Тень это условие работ, не событие: в ближайших событиях интервалы освещённости не показываются.
const upcoming = computed(() => upcomingEvents(filterEvents(events.value), selectedMs.value, 4));
const recommendation = computed(() => assessment.value?.recommendation || null);
// Обоснование обычными словами: решение и два следующих предложения `explanation` (lib/plain.ts: plainBrief);
// техническая запись `reason` в подсказке ссылки «подробнее». У расчётов без `explanation` она показывается как раньше.
const brief = computed(() => plainBrief(recommendation.value));

function toEvent(iso: string): void {
  setSelectedTime(iso);
  router.push('/events');
}

const sourceRows = computed(() => sources.value.slice(0, 7));
function sourceDot(s: (typeof sources.value)[number]): string {
  if (s.last_error) return 'red';
  if (s.state !== 'active') return 'yellow';
  if (s.cadence_s !== null && s.last_success_at && clock.value - ms(s.last_success_at) > 3 * s.cadence_s * 1000) return 'yellow';
  return 'green';
}
</script>
<template>
  <div class="dashboard-grid" :class="{ 'expanded-orbit': isOrbit }">
    <section class="panel scene-card with-scope" ref="sceneCard">
      <div class="card-header scene-header">
        <h2>Орбитальная обстановка</h2>
        <div class="scene-options">
          <div class="segmented">
            <button type="button" :class="{ selected: mode === '3D' }" :disabled="webglUnsupported" @click="mode = '3D'">3D</button>
            <button type="button" :class="{ selected: mode === '2D' }" @click="mode = '2D'">2D</button>
          </div>
          <button class="icon-btn" type="button" :aria-label="fullscreen ? 'Выйти из полноэкранного режима' : 'На весь экран'" @click="toggleFullscreen"><Icon :name="fullscreen ? 'Shrink' : 'Expand'" :size="19" /></button>
        </div>
      </div>
      <div class="scope-bar">
        <div class="segmented">
          <button type="button" :class="{ selected: inAssessment }" :disabled="!assessment" :title="assessment ? 'Трасса за период открытого расчёта' : 'Открытого расчёта нет'" @click="toAssessmentPeriod">Период расчёта</button>
          <button type="button" :class="{ selected: !inAssessment }" title="Трасса вокруг текущего момента" @click="toNow">Сейчас</button>
        </div>
        <div class="scope-period" v-if="inAssessment && assessment && trackRange">
          <strong>Расчёт {{ assessment.id }} · {{ MODE_LABEL[assessment.request.mode] }}</strong>
          <small>{{ periodCaption(assessment.request) }} · {{ num(periodHours, 1) }} ч<template v-if="assessment.request.cutoff"> · данные на {{ fmtUtc(assessment.request.cutoff) }}</template></small>
        </div>
        <div class="scope-period" v-else-if="trackRange">
          <strong>Сейчас · трасса без расчёта</strong>
          <small>{{ fmtUtc(trackRange.start) }} – {{ fmtUtc(trackRange.end) }} · уровни риска не показаны: расчёта на этот период нет</small>
        </div>
        <span class="scope-break" v-if="windows"></span>
        <div class="segmented scope-focus" v-if="windows">
          <button type="button" class="requested" :class="{ selected: focusKind === 'requested' }" title="Ярко показана трасса запрошенного окна" @click="focusKind = 'requested'; setSelectedTime(windows.requested.start)">{{ windows.moved || !windows.preferred ? 'Запрошенное' : 'Окно' }} {{ fmtTime(windows.requested.start) }}–{{ fmtTime(windows.requested.end) }} · {{ requestedMinutes }} мин</button>
          <button v-if="windows.moved && windows.preferred" type="button" class="preferred" :class="{ selected: focusKind === 'preferred' }" title="Ярко показана трасса рекомендованного окна" @click="focusKind = 'preferred'; setSelectedTime(windows.preferred.start)">Рекомендованное {{ fmtTime(windows.preferred.start) }}–{{ fmtTime(windows.preferred.end) }} · {{ preferredMinutes }} мин</button>
          <button type="button" :class="{ selected: focusKind === 'period' }" title="Вся трасса периода одинаково яркая" @click="focusKind = 'period'">Весь период</button>
        </div>
      </div>
      <OrbitScene v-if="mode === '3D' && !webglUnsupported" :samples="samples" :position="position" :selected-time="selectedMs" :layers="layers" :focus="focusSpan" :requested="windows?.requested" :preferred="windows?.preferred" :leveled="inAssessment" @select="router.push('/telemetry')" @unsupported="webglUnsupported = true; mode = '2D'" />
      <OrbitMap2D v-else :samples="samples" :position="position" :selected-time="selectedMs" :layers="layers" :focus="focusSpan" :requested="windows?.requested" :preferred="windows?.preferred" :passes="passes" :leveled="inAssessment" @pick="setSelectedTime($event)" @select="router.push('/telemetry')" />
      <div class="scene-status"><span class="dot" :class="trackStatus.dot"></span>{{ trackStatus.text }}<template v-if="track.epoch"><span class="scene-status-divider">/</span>эпоха {{ fmtUtc(track.epoch) }}</template></div>
      <div class="layer-controls">
        <label v-for="[key, name] in layerNames" :key="key"><input type="checkbox" v-model="layers[key]" :class="key" />{{ name }}</label>
      </div>
      <div class="coordinate-panel">
        <Icon name="Globe2" :size="40" />
        <dl>
          <div><dt>Широта</dt><dd>{{ station.point ? `${num(station.point.lat, 2)}°` : '—' }}</dd></div>
          <div><dt>Долгота</dt><dd>{{ station.point ? `${num(station.point.lon, 2)}°` : '—' }}</dd></div>
          <div><dt>Высота</dt><dd>{{ station.point ? `${num(station.point.alt, 1)} км` : '—' }}</dd></div>
          <div><dt>Скорость</dt><dd>{{ station.point?.speed != null ? `${num(station.point.speed, 2)} км/с` : '—' }}</dd></div>
          <div><dt>Освещённость</dt><dd>{{ station.sample && track.source !== 'sgp4' ? (station.sample.sunlit ? 'свет' : 'тень') : 'нет данных' }}</dd></div>
          <div v-if="inAssessment"><dt>Протоны</dt><dd :style="{ color: station.sample ? LEVEL_COLOR[station.sample.level] : undefined }">{{ station.sample ? LEVEL_LABEL[station.sample.level] : 'нет данных' }}</dd></div>
          <div v-if="inAssessment && station.sample && station.sample.mlat_deg !== null"><dt>Магн. широта</dt><dd>{{ num(station.sample.mlat_deg, 1) }}°</dd></div>
        </dl>
      </div>
      <span class="scene-hint"><Icon name="MousePointer2" :size="13" />{{ mode === '3D' ? 'Вращение · масштаб колёсиком' : 'Клик по трассе выбирает время' }}</span>
    </section>

    <div class="right-column">
      <section class="panel station-card">
        <div class="card-header">
          <h2>Параметры МКС</h2>
          <button class="status-text" :class="omm ? 'teal' : 'yellow'" type="button" @click="router.push('/telemetry')"><span class="dot" :class="omm ? 'teal' : 'yellow'"></span>{{ omm ? `OMM ${ago(omm.EPOCH, clock)}` : orbitError ? 'OMM недоступен' : 'OMM загружается' }}</button>
        </div>
        <div class="station-content">
          <button class="station-image" type="button" aria-label="Телеметрия МКС" @click="router.push('/telemetry')"><Station /><span>ISS / ZARYA <Icon name="ArrowUpRight" :size="12" /></span></button>
          <dl class="telemetry">
            <div><dt>Высота орбиты</dt><dd>{{ station.point ? num(station.point.alt, 1) : '—' }} <small>км</small></dd></div>
            <div><dt>Скорость</dt><dd>{{ station.point?.speed != null ? num(station.point.speed, 2) : '—' }} <small>км/с</small></dd></div>
            <div><dt>Наклонение <span :title="omm ? `OMM ${sourceLabel(orbit?.provenance?.source || '')}` : 'нет элементов'">· OMM</span></dt><dd>{{ omm ? `${num(Number(omm.INCLINATION), 2)}°` : '—' }}</dd></div>
            <div><dt>Период обращения · OMM</dt><dd>{{ omm ? num(periodMinutes(omm), 1) : '—' }} <small>мин</small></dd></div>
          </dl>
        </div>
      </section>

      <section class="panel risk-card">
        <div class="card-header">
          <h2>Оценка факторов</h2>
          <span class="muted" v-if="assessment">{{ MODE_LABEL[assessment.request.mode] }} · {{ assessment.id }}</span>
        </div>
        <div class="risk-content" v-if="assessment">
          <button class="gauge" type="button" :title="thresholdsHint" aria-label="Линий с данными" @click="router.push('/risks')">
            <svg viewBox="0 0 120 120"><circle cx="60" cy="60" r="49" class="gauge-track" /><circle cx="60" cy="60" r="49" class="gauge-value" :stroke-dasharray="`${(availableLines / Math.max(1, scores.length)) * 308} 308`" /></svg>
            <div><strong>{{ availableLines }}<span>/{{ scores.length }}</span></strong><small>линий<br />с данными</small></div>
          </button>
          <div class="factor-list">
            <button v-for="s in scores" :key="s.line" type="button" :title="`${s.label}: ${s.reason}.${unmeasuredLabel(s) ? ` ${unmeasuredLabel(s)}.` : ''} ${thresholdsHint}`" @click="router.push('/risks')">
              <span class="dot" :class="s.color"></span>
              <span>{{ s.label }}</span>
              <b class="score-pill" :class="s.color">{{ windowLabel(s) }}</b>
            </button>
          </div>
        </div>
        <div class="risk-content" v-else>
          <div class="empty-state small"><Icon name="ShieldCheck" :size="28" /><p>Открытого расчёта нет: оценка факторов появится после расчёта.</p></div>
        </div>
      </section>
    </div>
  </div>

  <section class="time-panel panel" v-if="trackRange">
    <div class="time-row">
      <button class="icon-btn" type="button" :aria-label="playing ? 'Пауза' : 'Воспроизведение'" @click="playing = !playing"><Icon :name="playing ? 'Pause' : 'Play'" /></button>
      <select v-model.number="speed" aria-label="Скорость воспроизведения"><option :value="1">1×</option><option :value="60">60×</option><option :value="600">600×</option></select>
      <PeriodStrip :range="trackRange" :runs="runs" :requested="windows?.requested" :preferred="windows?.preferred" :starts="starts" :selected="selectedMs" :leveled="inAssessment" @pick="setSelectedTime($event)" />
      <span class="mono">{{ fmtTime(selectedTime) }} UTC<br /><small>{{ fmtDate(selectedTime) }}</small></span>
    </div>
    <div class="pass-row" v-if="inAssessment">
      <span v-if="passes.length">Проходы высокого и критического уровня линии протонов, {{ passes.length }}:</span>
      <span v-else>В периоде расчёта нет минут высокого и критического уровня линии протонов.</span>
      <button v-for="p in passes" :key="p.start" type="button" class="pass-chip" :class="{ outside: !passInFocus(p) }" :title="`${LEVEL_LABEL[p.level]}${p.maxAbsMlat !== null ? `, магнитная широта до ${num(p.maxAbsMlat, 1)}°` : ''}; нажмите, чтобы поставить станцию на этот проход`" @click="setSelectedTime(p.start)"><i :style="{ background: LEVEL_COLOR[p.level] }"></i>{{ fmtTime(p.start) }}–{{ fmtTime(p.end) }}</button>
    </div>
  </section>

  <div class="bottom-grid">
    <section class="panel events-card">
      <div class="card-header"><h2>Ближайшие события</h2><RouterLink to="/events" class="text-btn">Перейти к всем ближайшим событиям <Icon name="ArrowRight" :size="15" /></RouterLink></div>
      <div class="event-list">
        <button v-for="e in upcoming" :key="e.id" type="button" class="event" @click="toEvent(e.start)">
          <span class="event-dot" :style="{ color: LEVEL_COLOR[e.level] }"></span>
          <time>{{ fmtTime(e.start) }}</time>
          <div><strong>{{ e.category }}: {{ e.note || e.rule.slice(0, 60) }}</strong><small><Badges :level="e.level" small /> <Badges :kind="e.kind" small /> · до {{ fmtTime(e.end) }} UTC</small></div>
        </button>
        <div v-if="!upcoming.length" class="empty-state small"><p>{{ assessment ? 'Интервалов выше фона в расчёте нет' : 'Открытого расчёта нет' }}</p></div>
      </div>
      <span class="card-footnote">Интервалы линий открытого расчёта · UTC</span>
    </section>

    <section class="panel eva-card">
      <div class="card-header"><h2>Окно для ВКД</h2><span class="status-text" :class="recommendation && !recommendation.insufficient ? 'teal' : 'yellow'"><span class="dot" :class="recommendation && !recommendation.insufficient ? 'teal' : 'yellow'"></span>{{ recommendation ? (recommendation.insufficient ? 'оснований недостаточно' : 'рекомендация') : 'нет расчёта' }}</span></div>
      <div class="next-window">
        <div class="window-icon"><Icon name="Clock4" :size="33" /></div>
        <div v-if="recommendation && recommendation.preferred && !recommendation.insufficient">
          <span class="muted">Предпочтительное окно</span>
          <strong>{{ fmtTime(recommendation.preferred.start) }} — {{ fmtTime(recommendation.preferred.end) }} <small>UTC</small></strong>
          <small>{{ fmtDate(recommendation.preferred.start) }} · {{ assessment!.request.duration_hours }} ч · равнозначных {{ recommendation.equivalents.length }}</small>
        </div>
        <div v-else-if="recommendation">
          <span class="muted">Рекомендация</span>
          <strong class="red">Оснований недостаточно</strong>
          <small v-if="!brief">{{ recommendation.reason }}</small>
        </div>
        <div v-else>
          <span class="muted">Расчёт не выполнен</span>
          <strong>—</strong>
          <small>Задайте окно и период в планировании</small>
        </div>
      </div>
      <div class="window-details" v-if="assessment">
        <Icon name="Rocket" :size="19" />
        <div><span>Запрошено</span><small>{{ fmtUtc(assessment.requested_window.window.start) }} – {{ fmtTime(assessment.requested_window.window.end) }}{{ assessment.requested_window.excluded_reason ? ` · ${assessment.requested_window.excluded_reason}` : '' }}</small></div>
        <Icon name="ClipboardCheck" :size="19" />
        <div v-if="brief" class="plain-brief">
          <span>Обоснование</span>
          <p class="plain-brief-head" :class="{ red: recommendation?.insufficient }"><PlainParts :parts="brief.headline.parts" /></p>
          <PlainList :items="brief.items" />
          <RouterLink to="/eva" class="plain-brief-more" :title="`Техническая запись: ${recommendation?.reason ?? ''}`">подробнее{{ brief.more ? ` · ещё пунктов: ${brief.more}` : '' }}</RouterLink>
        </div>
        <div v-else><span>Обоснование</span><small>{{ recommendation?.reason }}</small></div>
      </div>
      <RouterLink to="/eva" class="whole-card-link">Планирование выхода <Icon name="ArrowRight" :size="15" /></RouterLink>
    </section>

    <section class="panel sources-card">
      <div class="card-header"><h2>Источники данных</h2><RouterLink to="/sources" class="text-btn">Перейти к просмотру всех статусов <Icon name="ArrowRight" :size="15" /></RouterLink></div>
      <button v-for="s in sourceRows" :key="s.name" type="button" class="source-row" @click="router.push('/sources')">
        <Icon name="Database" :size="18" />
        <span>{{ sourceLabel(s.name) }}</span>
        <span class="dot" :class="sourceDot(s)"></span>
        <small>{{ s.state !== 'active' ? (s.state === 'frozen' ? 'заморожен' : 'отключён') : ago(s.last_success_at, clock) }}</small>
      </button>
      <div v-if="!sourceRows.length" class="empty-state small"><p>{{ sourcesError || 'Список источников пуст' }}</p></div>
    </section>
  </div>
</template>
