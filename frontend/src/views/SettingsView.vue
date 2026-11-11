<script setup lang="ts">
import { computed, onMounted } from 'vue';
import Icon from '../components/Icon.vue';
import { loadRules, rules, rulesError } from '../store/state';

onMounted(() => {
  if (!rules.value) loadRules();
});

type Entry = [string, unknown];

function isScalar(value: unknown): boolean {
  return value === null || value === undefined || ['string', 'number', 'boolean'].includes(typeof value) || Array.isArray(value);
}

function scalar(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'boolean') return value ? 'да' : 'нет';
  if (Array.isArray(value)) return value.map((v) => scalar(v)).join(', ');
  return String(value);
}

function isUrl(value: unknown): boolean {
  return typeof value === 'string' && /^https?:\/\/\S+$/.test(value);
}

// Каждый раздел ответа /rules раскладывается на скалярные строки и вложенные группы (например, sources.sep).
interface Section {
  key: string;
  title: string;
  rows: Entry[];
  groups: { key: string; rows: Entry[] }[];
}

const SECTION_LABEL: Record<string, string> = { windows: 'Сравнение окон (config/windows.toml)', sep: 'Линия протонов СЭЧ (config/sep_line.toml)', sources: 'Источники параметров' };

const sections = computed<Section[]>(() => {
  if (!rules.value) return [];
  return Object.entries(rules.value).map(([key, value]) => {
    const entries: Entry[] = value && typeof value === 'object' ? Object.entries(value as Record<string, unknown>) : [];
    return {
      key,
      title: SECTION_LABEL[key] || key,
      rows: entries.filter(([, v]) => isScalar(v)),
      groups: entries.filter(([, v]) => !isScalar(v)).map(([k, v]) => ({ key: k, rows: Object.entries(v as Record<string, unknown>) })),
    };
  });
});
</script>
<template>
  <div class="section-toolbar">
    <span class="muted">Правила расчёта только для чтения; изменения вносятся в конфигурацию сервера</span>
    <button class="button" type="button" @click="loadRules"><Icon name="RefreshCw" :size="16" />Обновить</button>
  </div>
  <div v-if="rulesError" class="panel info-banner"><Icon name="Unplug" /><div><strong>Правила недоступны</strong><p>{{ rulesError }}</p></div></div>
  <div class="settings-grid" v-if="sections.length">
    <section class="panel settings-section" v-for="section in sections" :key="section.key">
      <h2>{{ section.title }}</h2>
      <dl class="kv" v-if="section.rows.length">
        <div v-for="[name, value] in section.rows" :key="name"><dt>{{ name }}</dt><dd class="mono"><a v-if="isUrl(value)" :href="String(value)" target="_blank" rel="noopener">{{ value }}</a><template v-else>{{ scalar(value) }}</template></dd></div>
      </dl>
      <template v-for="group in section.groups" :key="group.key">
        <h3>{{ group.key }}</h3>
        <dl class="kv">
          <div v-for="[name, value] in group.rows" :key="name"><dt>{{ name }}</dt><dd :class="{ mono: !isUrl(value) }"><a v-if="isUrl(value)" :href="String(value)" target="_blank" rel="noopener">{{ value }}</a><template v-else>{{ scalar(value) }}</template></dd></div>
        </dl>
      </template>
    </section>
  </div>
  <section class="panel settings">
    <div>
      <h3>Дизайн-система</h3>
      <p>Палитра, типографика, компоненты и состояния интерфейса.</p>
      <RouterLink class="button" to="/design">Открыть компоненты <Icon name="ArrowUpRight" :size="16" /></RouterLink>
    </div>
  </section>
</template>
