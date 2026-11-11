<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ago, fmtDate, fmtTime, fmtUtc } from '../lib/format';
import { MODE_LABEL } from '../lib/labels';
import { assessment, clock, health, healthError, oldestSuccess, selectedTime, setSelectedTime, sources, sourcesError } from '../store/state';
import DateTimeModal from './DateTimeModal.vue';
import Icon from './Icon.vue';

const emit = defineEmits<{ menu: [] }>();
const route = useRoute();
const router = useRouter();

const titles: Record<string, string> = {
  '/': 'Орбитальная обстановка', '/orbit': 'Орбитальная обстановка', '/eva': 'Планирование ВКД', '/compare': 'Сравнение окон ВКД',
  '/risks': 'Анализ рисков', '/events': 'Ближайшие события', '/sources': 'Мониторинг источников', '/telemetry': 'Телеметрия МКС',
  '/objects': 'Объекты на орбите', '/reports': 'Отчёты', '/archive': 'Архив расчётов', '/settings': 'Настройки', '/design': 'Дизайн-система',
};
const subtitles: Record<string, string> = {
  '/': 'Положение станции, траектория и оценка факторов по последнему расчёту',
  '/orbit': 'Трасса станции за период открытого расчёта: окна выхода, уровни и проходы; без расчёта трасса вокруг текущего момента',
  '/eva': 'Режим, окно ВКД, период допустимого начала и ограничения; интервалы по линиям и рекомендация',
  '/compare': 'Окна одинаковой длительности с началом в периоде допустимого начала и правила их сравнения',
  '/risks': 'Линии риска открытого расчёта: доступность данных, уверенность, интервалы, правила и источники',
  '/events': 'Интервалы уровня выше фона по всем линиям в хронологии',
  '/sources': 'Состояние адаптеров, давность записей и управление опросом',
  '/telemetry': 'Параметры станции на выбранное время и их происхождение',
  '/objects': 'Элементы орбиты МКС, текущий отчёт сближений CelesTrak SOCRATES и линия сближений открытого расчёта',
  '/reports': 'Сохранённые расчёты сервера, выгрузка zip и JSON',
  '/archive': 'Ссылки на расчёты, сохранённые в этом браузере',
  '/settings': 'Правила расчёта и параметры источников только для чтения',
};

// Раздел документации открывается по адресам вида /manual/<документ>: заголовок общий для всех документов.
const isManual = computed(() => route.path === '/manual' || route.path.startsWith('/manual/'));
const title = computed(() => (isManual.value ? 'Документация' : titles[route.path] || 'Орбитальная обстановка'));
const subtitle = computed(() =>
  isManual.value
    ? 'Модель, API, источники, результаты и журнал решений; пояснения терминов ссылаются сюда'
    : subtitles[route.path] || 'Система поддержки решений при планировании внекорабельной деятельности',
);

const search = ref('');
const searchResults = computed(() => {
  const query = search.value.trim().toLowerCase();
  if (!query) return [];
  const seen = new Set<string>();
  return Object.entries(titles).filter(([path, name]) => {
    if (seen.has(name) || path === '/orbit') return false;
    seen.add(name);
    return name.toLowerCase().includes(query);
  });
});
watch(route, () => (search.value = ''));

const dateOpen = ref(false);

const freshness = computed(() => {
  if (sourcesError.value) return { dot: 'red', text: 'API недоступен', note: sourcesError.value };
  if (!sources.value.length) return { dot: 'muted', text: 'Нет источников', note: 'Список источников пуст' };
  const stale = sources.value.filter((s) => s.state === 'active' && s.cadence_s !== null && s.last_success_at && clock.value - new Date(s.last_success_at).getTime() > 3 * s.cadence_s * 1000);
  const errors = sources.value.filter((s) => s.last_error);
  const dot = errors.length || stale.length ? 'yellow' : 'green';
  const text = oldestSuccess.value ? ago(oldestSuccess.value, clock.value) : 'нет успешных опросов';
  const tick = health.value?.worker_last_tick ? `воркер: тик ${ago(health.value.worker_last_tick, clock.value)}` : healthError.value ? 'воркер: состояние неизвестно' : 'воркер не запускался';
  return { dot, text, note: errors.length ? `${errors.length} с ошибкой · ${tick}` : tick };
});

const lastLabel = computed(() => {
  const a = assessment.value;
  if (!a) return { text: 'Нет открытого расчёта', note: 'Запустите расчёт в планировании' };
  // Расчёт «сейчас» обновляется автоматически: видно, когда он посчитан.
  const updated = a.request.mode === 'current' ? ` · обновлено ${fmtTime(a.created_at)} UTC` : '';
  return { text: `${MODE_LABEL[a.request.mode]} · ${fmtTime(a.request.eva_start)} UTC`, note: `${fmtDate(a.request.eva_start)} · ${a.request.duration_hours} ч${updated} · ${a.id}` };
});
</script>
<template>
  <header class="page-header">
    <button class="icon-btn menu-button" aria-label="Открыть меню" @click="emit('menu')"><Icon name="Menu" /></button>
    <div class="heading">
      <h1>{{ title }}</h1>
      <p>{{ subtitle }}</p>
    </div>
    <div class="header-actions">
      <div class="search">
        <Icon name="Search" :size="18" />
        <input v-model="search" placeholder="Поиск по разделам…" aria-label="Поиск по разделам" />
        <div v-if="search" class="search-results">
          <button v-for="[path, name] in searchResults" :key="path" type="button" @click="router.push(path)">{{ name }}<Icon name="ArrowUpRight" :size="16" /></button>
          <span v-if="!searchResults.length">Ничего не найдено</span>
        </div>
      </div>
      <span class="header-divider"></span>
      <div class="avatar" aria-label="Аналитик">АН</div>
    </div>
  </header>
  <section class="metrics">
    <div class="metric panel">
      <div class="metric-icon blue"><Icon name="Clock3" :size="33" /></div>
      <div><span class="muted">Текущее время (UTC)</span><strong class="time">{{ fmtTime(clock, true) }}</strong><small>{{ fmtDate(clock) }}</small></div>
    </div>
    <button class="metric panel" type="button" @click="router.push(assessment ? '/eva' : '/eva')">
      <div class="metric-icon indigo"><Icon name="Rocket" :size="33" /></div>
      <div><span class="muted">Открытый расчёт</span><strong class="wrap">{{ lastLabel.text }}</strong><small>{{ lastLabel.note }}</small></div>
    </button>
    <button class="metric panel" type="button" @click="router.push('/sources')">
      <div class="metric-icon teal"><Icon name="Database" :size="33" /></div>
      <div><span class="muted">Актуальность данных</span><strong class="freshness"><span class="dot" :class="freshness.dot"></span>{{ freshness.text }}</strong><small>{{ freshness.note }}</small></div>
    </button>
    <button class="metric panel date-metric" type="button" @click="dateOpen = true" aria-label="Изменить выбранное время расчёта">
      <div class="metric-icon indigo"><Icon name="CalendarClock" :size="31" /></div>
      <div><span class="muted">Выбранное время (UTC)</span><strong>{{ fmtUtc(selectedTime) }}</strong><small>Нажмите, чтобы изменить время расчёта</small></div>
    </button>
  </section>
  <DateTimeModal :open="dateOpen" :value="selectedTime" @close="dateOpen = false" @select="setSelectedTime($event)" />
</template>
