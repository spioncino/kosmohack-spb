<script setup lang="ts">
import { computed } from 'vue';
import { fmtUtc } from '../lib/format';
import { closeEvidence, evidence, evidenceError, evidenceLoading, evidenceOpen } from '../store/state';
import Icon from './Icon.vue';

const record = computed(() => evidence.value?.record || null);
const text = computed(() => (evidence.value?.content_text ?? '').slice(0, 20000));
const truncated = computed(() => (evidence.value?.content_text?.length || 0) > 20000);
</script>
<template>
  <div v-if="evidenceOpen" class="drawer-backdrop" @click.self="closeEvidence">
    <aside class="source-drawer" role="dialog" aria-modal="true" aria-label="Сырая запись источника">
      <div class="card-header">
        <span class="eyebrow">СЫРАЯ ЗАПИСЬ ИСТОЧНИКА</span>
        <button class="icon-btn" aria-label="Закрыть" @click="closeEvidence"><Icon name="X" /></button>
      </div>
      <Icon name="Database" :size="32" class="teal" />
      <template v-if="evidenceLoading">
        <h2>Загрузка записи…</h2>
      </template>
      <template v-else-if="evidenceError">
        <h2>Запись недоступна</h2>
        <p class="muted">{{ evidenceError }}</p>
      </template>
      <template v-else-if="record">
        <h2>{{ record.source }} / {{ record.product }}</h2>
        <p class="muted">Источник → запись хранилища → время доступности → расчёт</p>
        <dl class="provenance">
          <div><dt>raw_id</dt><dd class="mono">{{ record.id }}</dd></div>
          <div><dt>URL</dt><dd><a :href="record.url" target="_blank" rel="noopener">{{ record.url }}</a></dd></div>
          <div><dt>Получено</dt><dd>{{ fmtUtc(record.fetched_at, true) }}</dd></div>
          <div><dt>Доступно с</dt><dd>{{ record.published_at ? fmtUtc(record.published_at, true) : 'неизвестно' }} ({{ record.published_at_kind }})</dd></div>
          <div><dt>Версия данных</dt><dd>{{ record.data_version }}</dd></div>
          <div><dt>Размер</dt><dd>{{ record.size.toLocaleString('ru-RU') }} байт · {{ record.content_type }}</dd></div>
          <div><dt>SHA-256</dt><dd class="mono">{{ record.sha256 }}</dd></div>
          <div><dt>HTTP</dt><dd>{{ record.http_status }}</dd></div>
          <div v-if="record.notes"><dt>Заметки</dt><dd>{{ record.notes }}</dd></div>
        </dl>
        <a class="button" :href="evidence!.download_url" target="_blank" rel="noopener"><Icon name="Download" :size="16" />Скачать байты записи</a>
        <h3>Содержимое</h3>
        <pre v-if="evidence!.content_text !== null">{{ text }}{{ truncated ? '\n… (показаны первые 20 000 символов)' : '' }}</pre>
        <p v-else class="muted">Двоичный файл: содержимое доступно по ссылке выше.</p>
      </template>
    </aside>
  </div>
</template>
