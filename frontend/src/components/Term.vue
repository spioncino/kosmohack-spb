<script lang="ts">
import { ref as moduleRef } from 'vue';

// Открыто не больше одного пояснения на странице: идентификатор открытого общий для всех экземпляров.
const openTerm = moduleRef<string | null>(null);
</script>
<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { docRoute, glossaryEntry } from '../lib/glossary';
import Segments from './Segments.vue';

defineOptions({ inheritAttrs: false });

// Слово или значение с пунктирным подчёркиванием: щелчок или Enter открывает пояснение из глоссария и ссылку на
// раздел документации; Esc и щелчок вне окна закрывают. Внутри кнопки или ссылки (например, строка события на
// обзоре) пояснение не открывается и не перехватывает щелчок: слово показывается как есть.
// hint: пояснение к месту на странице, показывается над общим текстом глоссария.
const props = defineProps<{ term: string | null | undefined; hint?: string }>();

const entry = computed(() => glossaryEntry(props.term));
const uid = `term-${Math.random().toString(36).slice(2, 10)}`;
const root = ref<HTMLElement | null>(null);
const pop = ref<HTMLElement | null>(null);
const nested = ref(false);
const open = computed(() => openTerm.value === uid);
const place = ref({ top: 0, left: 0, above: false });

const active = computed(() => !!entry.value && !nested.value);

onMounted(() => {
  nested.value = !!root.value?.parentElement?.closest('button, a, summary');
});

const POP_WIDTH = 320;
const GAP = 8;

function position(): void {
  const el = root.value;
  if (!el) return;
  const box = el.getBoundingClientRect();
  const width = Math.min(POP_WIDTH, window.innerWidth - 2 * GAP);
  const left = Math.min(Math.max(GAP, box.left), window.innerWidth - width - GAP);
  const height = pop.value?.offsetHeight ?? 160;
  const above = box.bottom + GAP + height > window.innerHeight && box.top - GAP - height > 0;
  place.value = { top: above ? box.top - GAP - height : box.bottom + GAP, left, above };
}

function show(): void {
  openTerm.value = uid;
  position();
  nextTick(() => {
    position();
    pop.value?.focus({ preventScroll: true });
  });
}

function close(returnFocus = false): void {
  if (openTerm.value === uid) openTerm.value = null;
  if (returnFocus) root.value?.focus({ preventScroll: true });
}

function toggle(event: Event): void {
  if (!active.value) return;
  event.preventDefault();
  event.stopPropagation();
  if (open.value) close();
  else show();
}

function onKey(event: KeyboardEvent): void {
  if (!active.value) return;
  if (event.key === 'Enter' || event.key === ' ') toggle(event);
  else if (event.key === 'Escape' && open.value) {
    event.stopPropagation();
    close(true);
  }
}

function onPopKey(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    event.stopPropagation();
    close(true);
  }
}

function onDocPointer(event: Event): void {
  const target = event.target as Node | null;
  if (!target) return;
  if (root.value?.contains(target) || pop.value?.contains(target)) return;
  close();
}

// Переход по любой ссылке окна (документация, код решения) закрывает пояснение.
function onPopClick(event: MouseEvent): void {
  if ((event.target as Element | null)?.closest('a')) close();
}

function onFocusOut(event: FocusEvent): void {
  const next = event.relatedTarget as Node | null;
  if (next && (root.value?.contains(next) || pop.value?.contains(next))) return;
  if (next) close();
}

const reposition = () => open.value && position();

watch(open, (value) => {
  if (value) {
    document.addEventListener('pointerdown', onDocPointer, true);
    window.addEventListener('scroll', reposition, true);
    window.addEventListener('resize', reposition);
  } else {
    document.removeEventListener('pointerdown', onDocPointer, true);
    window.removeEventListener('scroll', reposition, true);
    window.removeEventListener('resize', reposition);
  }
});

onBeforeUnmount(() => {
  close();
  document.removeEventListener('pointerdown', onDocPointer, true);
  window.removeEventListener('scroll', reposition, true);
  window.removeEventListener('resize', reposition);
});
</script>
<template>
  <span
    v-if="active"
    ref="root"
    class="term"
    :class="{ open }"
    role="button"
    tabindex="0"
    aria-haspopup="dialog"
    :aria-expanded="open ? 'true' : 'false'"
    :aria-controls="open ? uid : undefined"
    v-bind="$attrs"
    @click="toggle"
    @keydown="onKey"
  ><slot>{{ entry?.title }}</slot></span>
  <span v-else ref="root" class="term-plain" v-bind="$attrs"><slot>{{ entry?.title }}</slot></span>
  <Teleport to="body">
    <div
      v-if="open && entry"
      :id="uid"
      ref="pop"
      class="term-pop"
      :class="{ above: place.above }"
      role="dialog"
      :aria-label="entry.title"
      tabindex="-1"
      :style="{ top: `${place.top}px`, left: `${place.left}px` }"
      @keydown="onPopKey"
      @click="onPopClick"
      @focusout="onFocusOut"
    >
      <strong class="term-pop-title">{{ entry.title }}</strong>
      <p v-if="hint" class="term-pop-hint">{{ hint }}</p>
      <p><Segments :text="entry.text" /></p>
      <RouterLink class="term-pop-link" :to="docRoute(entry)">Подробнее в документации <span aria-hidden="true">→</span></RouterLink>
    </div>
  </Teleport>
</template>
