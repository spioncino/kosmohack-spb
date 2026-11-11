<script setup lang="ts">
import { useRouter } from 'vue-router';
import Badges from '../components/Badges.vue';
import { notify } from '../store/state';

const router = useRouter();
const swatches: [string, string][] = [['Background', '#0b0d0f'], ['Surface', '#151719'], ['Border', '#292d30'], ['Primary', '#f1f3f4'], ['Cyan', '#6daee8'], ['Teal', '#5bd3bb'], ['Attention', '#e8bd70'], ['Critical', '#e87470']];
</script>
<template>
  <div class="design-heading">
    <span class="eyebrow">MISIS GO / DESIGN SYSTEM 01</span>
    <h2>Спокойствие. Точность. Контекст.</h2>
    <p>Единая система рабочего интерфейса орбитального мониторинга.</p>
  </div>
  <section class="panel showcase">
    <h2>Палитра</h2>
    <div class="swatches">
      <div v-for="[name, color] in swatches" :key="name" :style="{ '--swatch': color }"><i /><strong>{{ name }}</strong><span>{{ color }}</span></div>
    </div>
  </section>
  <div class="report-cards">
    <section class="panel">
      <h2>Типографика</h2>
      <h1>Орбитальная обстановка</h1>
      <p>Основной текст · системный гротеск</p>
      <strong class="mono big">14:28:36 UTC</strong>
      <small>Числовые значения · моноширинный шрифт</small>
    </section>
    <section class="panel">
      <h2>Контролы и состояния</h2>
      <div class="control-samples">
        <button class="button primary" type="button" @click="notify('Основное действие')">Основное действие</button>
        <button class="button" type="button" @click="notify('Вторичное действие')">Вторичное</button>
        <button class="button" type="button" disabled>Недоступно</button>
      </div>
      <div class="status-examples">
        <span class="teal">● Актуально</span><span class="yellow">● Устарело</span><span class="red">● Ошибка</span><span class="muted">○ Нет данных</span>
      </div>
      <div class="badge-row">
        <Badges kind="observation" /><Badges kind="external_forecast" /><Badges kind="computed" />
        <Badges level="none" /><Badges level="elevated" /><Badges level="high" /><Badges level="critical" /><Badges level="unknown" />
        <Badges availability="available" /><Badges availability="missing" /><Badges availability="stale" /><Badges availability="unusable_for_replay" />
      </div>
      <div class="badge-row">
        <span class="score-pill green">в окне только none · полные данные</span><span class="score-pill yellow">в окне elevated или unknown · неполные данные</span><span class="score-pill red">в окне high или critical</span><span class="score-pill gray">нет данных</span>
      </div>
      <button class="text-btn" type="button" @click="router.push('/sources')">Показать состояния источников →</button>
    </section>
  </div>
</template>
