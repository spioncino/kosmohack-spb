<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { errorText } from '../api/client';
import { manualDoc, manualIndex, type ManualEntry } from '../api/manual';
import Icon from '../components/Icon.vue';
import Segments from '../components/Segments.vue';
import { GLOSSARY, docRoute } from '../lib/glossary';
import { renderMarkdown, type RenderedDoc } from '../lib/markdown';
import { manualRoute } from '../lib/manualLinks';

// Раздел «Документация»: список документов (GET /manual) и документ по имени (GET /manual/{name}) с переходом к
// разделу по якорю: #/manual/MODEL.md#линии-риска, #/manual/DECISIONS.md#d70.
const route = useRoute();
const router = useRouter();

const index = ref<ManualEntry[]>([]);
const indexError = ref('');
const doc = ref<RenderedDoc | null>(null);
const docError = ref('');
const loading = ref(false);
const main = ref<HTMLElement | null>(null);

const name = computed(() => {
  const value = route.params.name;
  return Array.isArray(value) ? value.join('/') : value || '';
});
const anchor = computed(() => (route.hash ? route.hash.slice(1) : ''));
const toc = computed(() => (doc.value ? doc.value.headings.filter((h) => h.depth === 2 || h.depth === 3) : []));
const glossary = computed(() => Object.entries(GLOSSARY).map(([key, entry]) => ({ key, entry })));

onMounted(async () => {
  try {
    index.value = await manualIndex();
  } catch (error) {
    indexError.value = errorText(error);
  }
});

let requestId = 0;
async function load(current: string): Promise<void> {
  const id = ++requestId;
  doc.value = null;
  docError.value = '';
  if (!current) return;
  loading.value = true;
  try {
    const [source, known] = await Promise.all([manualDoc(current), manualIndex().catch(() => [] as ManualEntry[])]);
    if (id !== requestId) return;
    doc.value = renderMarkdown(source, current, { known: known.length ? known.map((e) => e.name) : undefined });
  } catch (error) {
    if (id === requestId) docError.value = errorText(error);
  } finally {
    if (id === requestId) loading.value = false;
  }
}

// Цель перехода подсвечивается: у hash-маршрутизации нет :target.
let highlighted: HTMLElement | null = null;
function scrollToAnchor(): void {
  highlighted?.classList.remove('md-target');
  highlighted = null;
  if (name.value && !doc.value) return;
  const target = anchor.value ? document.getElementById(anchor.value) : null;
  if (target && main.value?.contains(target)) {
    target.scrollIntoView({ block: 'start' });
    target.classList.add('md-target');
    highlighted = target;
  } else if (!anchor.value) {
    window.scrollTo({ top: 0 });
  }
}

watch(name, (value) => load(value), { immediate: true });
watch([doc, anchor], () => nextTick(scrollToAnchor));
watch(name, (value) => {
  if (!value) nextTick(scrollToAnchor);
});
onMounted(() => {
  if (!name.value) nextTick(scrollToAnchor);
});

// Ссылки внутри документа на раздел «Документация» идут через маршрутизатор; повторный щелчок по той же
// ссылке снова прокручивает к разделу.
function onArticleClick(event: MouseEvent): void {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  const link = (event.target as HTMLElement | null)?.closest('a');
  const href = link?.getAttribute('href') || '';
  if (!href.startsWith('#/manual/')) return;
  event.preventDefault();
  const to = href.slice(1);
  const resolved = router.resolve(to);
  if (resolved.path === route.path && resolved.hash === route.hash) scrollToAnchor();
  else router.push(to);
}

const currentTitle = computed(() => index.value.find((e) => e.name === name.value)?.title || doc.value?.title || name.value);
</script>
<template>
  <div class="section-toolbar">
    <div class="tab-links">
      <RouterLink to="/manual" :class="{ selected: !name }">Документы и глоссарий</RouterLink>
      <RouterLink v-if="name" :to="manualRoute(name)" class="selected">{{ name }}</RouterLink>
    </div>
  </div>

  <div class="manual-layout" :class="{ single: !name }">
    <aside v-if="name" class="panel manual-side">
      <template v-if="toc.length">
        <h2>Разделы</h2>
        <nav class="manual-toc" aria-label="Разделы документа">
          <RouterLink v-for="h in toc" :key="h.id" :to="manualRoute(name, h.id)" :class="[`depth-${h.depth}`, { active: h.id === anchor }]">{{ h.text }}</RouterLink>
        </nav>
      </template>
      <h3>Документы</h3>
      <p v-if="indexError" class="form-error"><Icon name="TriangleAlert" :size="16" />{{ indexError }}</p>
      <nav class="manual-docs" aria-label="Документы">
        <RouterLink v-for="entry in index" :key="entry.name" :to="manualRoute(entry.name)" :class="{ active: entry.name === name }">
          <span>{{ entry.title }}</span>
          <small class="mono">{{ entry.name }}</small>
        </RouterLink>
      </nav>
    </aside>

    <section ref="main" class="panel manual-main">
      <template v-if="name">
        <div v-if="loading" class="empty-state small"><Icon name="LoaderCircle" class="spinning" :size="26" /><p>Загрузка {{ name }}…</p></div>
        <div v-else-if="docError" class="empty-state small">
          <Icon name="FileText" :size="30" />
          <p>Документ {{ name }} не открыт: {{ docError }}</p>
          <RouterLink to="/manual" class="button">К списку документов</RouterLink>
        </div>
        <article v-else-if="doc" class="markdown-body" :aria-label="currentTitle" @click="onArticleClick" v-html="doc.html"></article>
      </template>
      <template v-else>
        <div class="manual-intro">
          <h2>Документация сервиса</h2>
          <p class="muted">Как считаются линии риска, сравнение окон и сверка, откуда параметры и почему приняты решения. Щелчок по подчёркнутому пунктиром слову на любой странице открывает пояснение и ссылку сюда.</p>
        </div>
        <p v-if="indexError" class="form-error"><Icon name="TriangleAlert" :size="16" />{{ indexError }}</p>
        <div class="manual-cards">
          <RouterLink v-for="entry in index" :key="entry.name" :to="manualRoute(entry.name)" class="manual-card">
            <Icon name="FileText" :size="18" />
            <span><strong>{{ entry.title }}</strong><small class="mono">{{ entry.name }}</small></span>
          </RouterLink>
        </div>
        <h2 id="glossary" class="manual-glossary-title">Глоссарий</h2>
        <dl class="manual-glossary">
          <div v-for="item in glossary" :key="item.key">
            <dt>{{ item.entry.title }}</dt>
            <dd><Segments :text="item.entry.text" /> <RouterLink :to="docRoute(item.entry)" class="text-btn">{{ item.entry.doc.file }} → раздел</RouterLink></dd>
          </div>
        </dl>
      </template>
    </section>
  </div>
</template>
