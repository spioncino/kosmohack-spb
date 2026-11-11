<script setup lang="ts">
import { computed } from 'vue';
import { useRoute } from 'vue-router';
import { healthError, sources, sourcesError } from '../store/state';
import Icon from './Icon.vue';

defineProps<{ open: boolean }>();
const route = useRoute();

const nav = [
  { path: '/', name: 'Орбитальная обстановка', icon: 'Orbit', matches: ['/', '/orbit'] },
  { path: '/eva', name: 'Планирование ВКД', icon: 'CalendarDays', matches: ['/eva', '/compare'] },
  { path: '/risks', name: 'Анализ рисков', icon: 'ShieldCheck', matches: ['/risks', '/events'] },
  { path: '/telemetry', name: 'Телеметрия МКС', icon: 'ChartNoAxesColumnIncreasing', matches: ['/telemetry'] },
  { path: '/objects', name: 'Объекты на орбите', icon: 'Satellite', matches: ['/objects'] },
  { path: '/sources', name: 'Мониторинг источников', icon: 'Database', matches: ['/sources'] },
  { path: '/reports', name: 'Отчёты', icon: 'FileText', matches: ['/reports'] },
  { path: '/archive', name: 'Архив расчётов', icon: 'FolderArchive', matches: ['/archive'] },
  { path: '/manual', name: 'Документация', icon: 'BookOpen', matches: ['/manual'], prefix: '/manual/' },
];

// Раздел активен на своих путях, а «Документация» и на любом документе (#/manual/<имя>).
const isActive = (item: (typeof nav)[number]) => item.matches.includes(route.path) || (!!item.prefix && route.path.startsWith(item.prefix));

const degraded = computed(() => !!sourcesError.value || !!healthError.value || sources.value.some((s) => s.last_error));
const problemCount = computed(() => sources.value.filter((s) => s.last_error || s.state !== 'active').length);
</script>
<template>
  <aside class="sidebar" :class="{ open }">
    <RouterLink to="/" class="brand" aria-label="MISIS GO">MISIS GO</RouterLink>
    <div class="workspace-label">ПЛАНИРОВАНИЕ ВКД</div>
    <nav>
      <RouterLink v-for="item in nav" :key="item.path" :to="item.path" :class="['nav-link', { active: isActive(item) }]">
        <Icon :name="item.icon" :size="22" />
        <span>{{ item.name }}</span>
      </RouterLink>
    </nav>
    <div class="sidebar-bottom">
      <RouterLink to="/sources" class="connection">
        <span class="dot" :class="degraded ? 'yellow' : 'teal'"></span>
        <span>{{ sourcesError ? 'API недоступен' : problemCount ? `Источники: ${problemCount} с замечаниями` : 'Источники в норме' }}</span>
        <Icon name="ChevronRight" :size="14" />
      </RouterLink>
      <RouterLink to="/settings" class="nav-link"><Icon name="Settings" /><span>Настройки</span></RouterLink>
      <div class="profile">
        <div class="avatar">АН</div>
        <div><strong>Аналитик</strong><small>Группа планирования ВКД</small></div>
      </div>
    </div>
  </aside>
</template>
