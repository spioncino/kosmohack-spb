<script setup lang="ts">
import { computed, ref } from 'vue';
import Badges from '../components/Badges.vue';
import DateTimeModal from '../components/DateTimeModal.vue';
import Icon from '../components/Icon.vue';
import ProvenanceList from '../components/ProvenanceList.vue';
import Station from '../components/Station.vue';
import { ago, fmtUtc, num } from '../lib/format';
import { periodMinutes } from '../lib/orbit';
import { assessment, clock, orbit, orbitError, selectedTime, setSelectedTime, station, track, trajectory, trajectoryError } from '../store/state';

const dateOpen = ref(false);
const point = computed(() => station.value.point);
const sample = computed(() => station.value.sample);
const omm = computed(() => orbit.value?.omm || null);

interface Value { icon: string; label: string; value: string; unit: string; source: string }

const values = computed<Value[]>(() => {
  const p = point.value;
  const s = sample.value;
  const posSource = station.value.positionSource === 'sgp4' ? 'SGP4 по OMM' : station.value.positionSource === 'trajectory' ? `трасса ${track.value.label}` : 'нет данных';
  const trackSource = s ? track.value.label : 'нет сэмпла трассы на это время';
  return [
    { icon: 'MapPin', label: 'Широта', value: p ? num(p.lat, 4) : '—', unit: '°', source: posSource },
    { icon: 'Globe', label: 'Долгота', value: p ? num(p.lon, 4) : '—', unit: '°', source: posSource },
    { icon: 'MoveVertical', label: 'Высота', value: p ? num(p.alt, 2) : '—', unit: 'км', source: posSource },
    { icon: 'Gauge', label: 'Скорость', value: p && p.speed !== null ? num(p.speed, 3) : '—', unit: 'км/с', source: station.value.speedSource === 'sgp4' ? 'SGP4 по OMM, инерциальная' : station.value.speedSource === 'samples' ? 'по соседним сэмплам трассы' : 'нет данных' },
    { icon: s ? (s.sunlit ? 'Sun' : 'Moon') : 'Eclipse', label: 'Освещённость', value: s && track.value.source !== 'sgp4' ? (s.sunlit ? 'свет' : 'тень') : 'нет данных', unit: '', source: s && track.value.source !== 'sgp4' ? trackSource : 'нужна трасса из /trajectory' },
    { icon: 'Magnet', label: 'Геомагнитная широта', value: s && s.mlat_deg !== null ? num(s.mlat_deg, 2) : 'нет данных', unit: s && s.mlat_deg !== null ? '° AACGM' : '', source: s && s.mlat_deg !== null ? trackSource : 'вне области AACGM или нет трассы' },
    { icon: 'Radiation', label: 'ЮАА', value: s && track.value.source !== 'sgp4' ? (s.in_saa ? 'внутри аномалии' : 'вне аномалии') : 'нет данных', unit: '', source: s && track.value.source !== 'sgp4' ? trackSource : 'нужна трасса из /trajectory' },
    { icon: 'Activity', label: 'Уровень SEP в точке', value: s && s.level !== 'unknown' ? s.level : 'нет данных', unit: '', source: s && s.level !== 'unknown' ? 'линия протонов СЭЧ открытого расчёта' : 'вне интервалов линии или расчёт не открыт' },
  ];
});

const trajectoryInfo = computed(() => {
  if (track.value.source === 'assessment' && assessment.value) return { title: 'Траектория из открытого расчёта', t: assessment.value.trajectory };
  if (track.value.source === 'trajectory' && trajectory.value) return { title: 'Траектория из GET /trajectory', t: trajectory.value };
  return null;
});
</script>
<template>
  <div class="section-toolbar">
    <span class="muted">Значения на выбранное время {{ fmtUtc(selectedTime) }} · текущее время {{ fmtUtc(clock, true) }}</span>
    <div class="toolbar-actions">
      <button class="button" type="button" @click="dateOpen = true"><Icon name="CalendarClock" :size="16" />Выбрать время</button>
      <button class="button" type="button" @click="setSelectedTime(Date.now())"><Icon name="Clock3" :size="16" />Сейчас</button>
    </div>
  </div>
  <div class="telemetry-large">
    <section class="panel">
      <Station />
      <h2>{{ omm?.OBJECT_NAME || 'Международная космическая станция' }}</h2>
      <p>NORAD {{ omm?.NORAD_CAT_ID || '25544' }} · ISS / ZARYA</p>
      <dl class="kv" v-if="omm">
        <div><dt>Эпоха OMM</dt><dd class="mono">{{ fmtUtc(omm.EPOCH, true) }} · {{ ago(omm.EPOCH, clock) }}</dd></div>
        <div><dt>Наклонение</dt><dd class="mono">{{ num(Number(omm.INCLINATION), 4) }}°</dd></div>
        <div><dt>Период обращения</dt><dd class="mono">{{ num(periodMinutes(omm), 2) }} мин</dd></div>
        <div><dt>Эксцентриситет</dt><dd class="mono">{{ Number(omm.ECCENTRICITY).toExponential(3) }}</dd></div>
        <div v-if="orbit?.provenance"><dt>Источник</dt><dd><ProvenanceList :items="[orbit.provenance]" compact /></dd></div>
      </dl>
      <p v-else class="muted">Орбитальные элементы недоступны{{ orbitError ? `: ${orbitError}` : '' }}.</p>
    </section>
    <section class="panel telemetry-values icons">
      <div v-for="v in values" :key="v.label" :class="{ 'no-data': v.value === 'нет данных' || v.value === '—' }">
        <Icon :name="v.icon" :size="26" class="value-icon" />
        <span>{{ v.label }}</span>
        <strong>{{ v.value }} <small>{{ v.unit }}</small></strong>
        <small class="value-source">{{ v.source }}</small>
      </div>
    </section>
  </div>
  <section class="panel trajectory-info">
    <div class="card-header"><h2>Источник траектории</h2><Badges v-if="trajectoryInfo" :kind="'computed'" small /></div>
    <template v-if="trajectoryInfo">
      <dl class="kv columns">
        <div><dt>{{ trajectoryInfo.title }}</dt><dd>{{ trajectoryInfo.t.provider.toUpperCase() }} · {{ trajectoryInfo.t.is_reconstruction ? 'реконструкция' : 'прогноз на момент данных' }}</dd></div>
        <div><dt>Эпоха эфемериды</dt><dd class="mono">{{ fmtUtc(trajectoryInfo.t.epoch, true) }}</dd></div>
        <div><dt>Давность на момент запроса</dt><dd>{{ trajectoryInfo.t.age_at_query_hours === null ? '—' : `${num(trajectoryInfo.t.age_at_query_hours, 1)} ч` }}</dd></div>
        <div><dt>Сэмплов</dt><dd class="mono">{{ trajectoryInfo.t.samples.length }} · {{ fmtUtc(trajectoryInfo.t.samples[0]?.t) }} – {{ fmtUtc(trajectoryInfo.t.samples[trajectoryInfo.t.samples.length - 1]?.t) }}</dd></div>
        <div><dt>Запись</dt><dd><ProvenanceList :items="[trajectoryInfo.t.provenance]" compact /></dd></div>
      </dl>
      <ul class="limits" v-if="'limitations' in trajectoryInfo.t && (trajectoryInfo.t as any).limitations?.length"><li v-for="l in (trajectoryInfo.t as any).limitations" :key="l">{{ l }}</li></ul>
    </template>
    <p v-else class="muted panel-note">{{ track.source === 'sgp4' ? `Трасса из /trajectory недоступна (${trajectoryError || 'нет ответа'}); координаты по SGP4 из OMM, освещённость, магнитная широта и ЮАА не рассчитываются в браузере.` : `Траектория недоступна${trajectoryError ? `: ${trajectoryError}` : ''}.` }}</p>
  </section>
  <DateTimeModal :open="dateOpen" :value="selectedTime" title="Время телеметрии" @close="dateOpen = false" @select="setSelectedTime($event)" />
</template>
