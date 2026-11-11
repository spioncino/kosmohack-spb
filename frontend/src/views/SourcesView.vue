<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { api, errorText, waitJob, type SourceStateName } from '../api/client';
import Icon from '../components/Icon.vue';
import { ago, cadence, fmtUtc } from '../lib/format';
import { STATE_LABEL, STEP_LABEL, sourceLabel } from '../lib/labels';
import { clock, health, healthError, loadHealth, loadSources, notify, sources, sourcesError, sourcesLoadedAt } from '../store/state';

const reason = ref('');
const busy = ref<Record<string, string>>({});
const rowError = ref<Record<string, string>>({});

onMounted(() => {
  loadSources();
  loadHealth();
});

const ACTIONS: { state: SourceStateName; label: string; icon: string }[] = [
  { state: 'frozen', label: 'заморозить', icon: 'Snowflake' },
  { state: 'disabled', label: 'отключить', icon: 'Power' },
  { state: 'active', label: 'активировать', icon: 'Check' },
];

async function setState(name: string, state: SourceStateName): Promise<void> {
  const text = reason.value.trim();
  if (!text) {
    rowError.value = { ...rowError.value, [name]: 'Укажите причину в поле над таблицей' };
    return;
  }
  busy.value = { ...busy.value, [name]: STATE_LABEL[state] };
  try {
    await api.setSourceState(name, state, text);
    rowError.value = { ...rowError.value, [name]: '' };
    notify(`${name}: ${STATE_LABEL[state]}`);
  } catch (error) {
    rowError.value = { ...rowError.value, [name]: errorText(error) };
  } finally {
    busy.value = { ...busy.value, [name]: '' };
    await loadSources();
  }
}

async function refresh(name: string): Promise<void> {
  busy.value = { ...busy.value, [name]: 'задание…' };
  try {
    const created = await api.refreshSource(name);
    const finished = await waitJob(created.job_id, {
      onProgress: (j) => {
        busy.value = { ...busy.value, [name]: j.status === 'queued' ? `в очереди ${j.id}` : `${STEP_LABEL[j.progress?.step || ''] || j.progress?.step || 'выполняется'}` };
        if (j.status === 'queued') loadHealth();
      },
    });
    if (finished.status === 'failed') rowError.value = { ...rowError.value, [name]: `задание ${finished.id} не выполнено: ${finished.error}` };
    else {
      rowError.value = { ...rowError.value, [name]: '' };
      notify(`${name}: обновлено`);
    }
  } catch (error) {
    rowError.value = { ...rowError.value, [name]: errorText(error) };
  } finally {
    busy.value = { ...busy.value, [name]: '' };
    await loadSources();
  }
}

const workerText = computed(() => (health.value?.worker_last_tick ? `воркер: тик ${ago(health.value.worker_last_tick, clock.value)}` : healthError.value ? `воркер: состояние неизвестно (${healthError.value})` : 'воркер не запускался'));

function stale(s: (typeof sources.value)[number]): boolean {
  return s.state === 'active' && s.cadence_s !== null && !!s.last_success_at && clock.value - new Date(s.last_success_at).getTime() > 3 * s.cadence_s * 1000;
}
</script>
<template>
  <div class="section-toolbar">
    <p class="muted">{{ sources.length }} источников · {{ workerText }}<span v-if="sourcesLoadedAt"> · список на {{ fmtUtc(sourcesLoadedAt, true) }}</span></p>
    <div class="toolbar-actions">
      <input v-model="reason" class="reason-input" placeholder="Причина для смены состояния (обязательна)" aria-label="Причина смены состояния" />
      <button class="button" type="button" @click="loadSources(); loadHealth()"><Icon name="RefreshCw" :size="16" />Обновить список</button>
    </div>
  </div>

  <div v-if="sourcesError" class="panel info-banner error"><Icon name="WifiOff" /><div><strong>Список источников недоступен</strong><p>{{ sourcesError }}</p></div></div>

  <section class="panel table-panel sources-table" v-if="sources.length">
    <table>
      <thead>
        <tr><th>Источник</th><th>Продукты</th><th>Опрос</th><th>Состояние</th><th>Причина</th><th>Последний успех</th><th>Последняя запись</th><th>Записей</th><th>Ошибка</th><th>Действия</th></tr>
      </thead>
      <tbody>
        <tr v-for="s in sources" :key="s.name" :class="[`state-${s.state}`, { stale: stale(s) }]">
          <td><span class="dot" :class="s.last_error ? 'red' : s.state !== 'active' ? 'yellow' : stale(s) ? 'yellow' : 'green'"></span><div><strong>{{ s.name }}</strong><small>{{ sourceLabel(s.name) }}<template v-if="s.supports_archive"> · архив</template></small></div></td>
          <td class="wrap"><small>{{ s.products.join(', ') }}</small></td>
          <td>{{ s.cadence_s === null ? 'архив, без обновления' : cadence(s.cadence_s) }}</td>
          <td><span class="state-pill" :class="s.state">{{ STATE_LABEL[s.state] }}</span><small v-if="s.changed_at"> с {{ fmtUtc(s.changed_at) }}</small></td>
          <td class="wrap"><small>{{ s.reason || '' }}</small></td>
          <td :title="s.last_success_at || ''">{{ ago(s.last_success_at, clock) }}<small v-if="stale(s)"> · старше 3 периодов</small></td>
          <td :title="s.last_record_fetched_at || ''">{{ ago(s.last_record_fetched_at, clock) }}</td>
          <td class="mono">{{ s.records }}</td>
          <td class="wrap error-cell"><small>{{ s.last_error || '' }}</small></td>
          <td>
            <div class="row-actions">
              <button v-for="a in ACTIONS.filter((x) => x.state !== s.state)" :key="a.state" type="button" class="button small" :disabled="!!busy[s.name]" @click="setState(s.name, a.state)"><Icon :name="a.icon" :size="13" />{{ a.label }}</button>
              <button type="button" class="button small" :disabled="s.archive_only || s.state !== 'active' || !!busy[s.name]" :title="s.archive_only ? 'архивный источник: адаптера обновления нет' : ''" @click="refresh(s.name)"><Icon name="RotateCw" :size="13" :class="{ spinning: !!busy[s.name] }" />обновить</button>
            </div>
            <small v-if="busy[s.name]" class="teal">{{ busy[s.name] }}</small>
            <small v-if="rowError[s.name]" class="red">{{ rowError[s.name] }}</small>
          </td>
        </tr>
      </tbody>
    </table>
  </section>

</template>
