<script setup lang="ts">
import { useRouter } from 'vue-router';
import Icon from '../components/Icon.vue';
import { fmtUtc } from '../lib/format';
import { MODE_LABEL } from '../lib/labels';
import { periodCaption } from '../lib/period';
import { addToArchive, archive, assessment, notify, openAssessment, removeFromArchive } from '../store/state';

const router = useRouter();

async function open(id: string): Promise<void> {
  if (await openAssessment(id)) router.push('/eva');
  else notify('Расчёт не найден на сервере: ссылка из архива устарела');
}

function saveCurrent(): void {
  if (!assessment.value) return;
  notify(addToArchive(assessment.value) ? 'Ссылка сохранена в архив этого браузера' : 'Хранилище браузера недоступно');
}
</script>
<template>
  <div class="section-toolbar">
    <button class="button primary" type="button" :disabled="!assessment" @click="saveCurrent"><Icon name="Save" :size="16" />Сохранить открытый расчёт</button>
  </div>
  <section class="panel archive-table">
    <div class="card-header"><h2>Сохранено в этом браузере</h2><span class="muted">{{ archive.length }}</span></div>
    <div v-for="item in archive" :key="item.id" class="saved-row">
      <Icon name="FolderArchive" />
      <div>
        <strong>{{ MODE_LABEL[item.request.mode] }} · ВКД {{ fmtUtc(item.request.eva_start) }} · {{ item.request.duration_hours }} ч</strong>
        <small>{{ periodCaption(item.request) }}<template v-if="item.request.cutoff"> · отсечение {{ fmtUtc(item.request.cutoff) }}</template> · сохранено {{ fmtUtc(item.savedAt) }} · {{ item.id }}</small>
      </div>
      <span :class="item.insufficient ? 'red' : 'teal'">{{ item.insufficient || !item.preferred ? 'оснований недостаточно' : `рекомендовано ${fmtUtc(item.preferred.start)}` }}</span>
      <button class="button" type="button" @click="open(item.id)">Открыть</button>
      <button class="icon-btn" type="button" aria-label="Удалить из архива" @click="removeFromArchive(item.id)"><Icon name="Trash2" :size="16" /></button>
    </div>
    <div class="empty-state" v-if="!archive.length">
      <Icon name="Archive" :size="38" />
      <h3>Архив пуст</h3>
      <p>Каждый запущенный расчёт добавляется сюда автоматически; открытый расчёт можно сохранить кнопкой выше.</p>
    </div>
  </section>
</template>
