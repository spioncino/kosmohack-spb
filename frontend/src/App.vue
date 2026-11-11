<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import EvidenceModal from './components/EvidenceModal.vue';
import Icon from './components/Icon.vue';
import PageHeader from './components/PageHeader.vue';
import Sidebar from './components/Sidebar.vue';
import { autoRefreshTick, bootstrap, clock, loadHealth, loadSources, toast } from './store/state';

const route = useRoute();
const mobileNav = ref(false);
let timer: ReturnType<typeof setInterval> | undefined;
let poll: ReturnType<typeof setInterval> | undefined;
let refresh: ReturnType<typeof setInterval> | undefined;

// Возврат на вкладку: сначала свежее состояние источников, потом проверка автообновления расчёта «сейчас».
async function onVisibility(): Promise<void> {
  if (document.visibilityState === 'hidden') return;
  await loadSources();
  autoRefreshTick();
}

onMounted(() => {
  bootstrap();
  timer = setInterval(() => (clock.value = Date.now()), 1000);
  poll = setInterval(() => {
    loadHealth();
    loadSources();
  }, 60000);
  refresh = setInterval(() => autoRefreshTick(), 30000);
  document.addEventListener('visibilitychange', onVisibility);
});
onUnmounted(() => {
  clearInterval(timer);
  clearInterval(poll);
  clearInterval(refresh);
  document.removeEventListener('visibilitychange', onVisibility);
});
watch(route, () => (mobileNav.value = false));
</script>
<template>
  <div class="app-shell">
    <Sidebar :open="mobileNav" />
    <main class="main">
      <PageHeader @menu="mobileNav = !mobileNav" />
      <RouterView />
      <footer class="main-footer">
        <span><span class="dot teal"></span>MISIS GO <i>/</i> Система поддержки решений при планировании ВКД</span>
      </footer>
    </main>
    <EvidenceModal />
    <div v-if="toast" class="toast"><Icon name="CheckCircle2" :size="19" />{{ toast }}</div>
  </div>
</template>
