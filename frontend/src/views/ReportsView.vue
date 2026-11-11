<script setup lang="ts">
import { onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { exportUrl, jsonUrl } from '../api/client';
import Icon from '../components/Icon.vue';
import { fmtUtc } from '../lib/format';
import { MODE_LABEL } from '../lib/labels';
import { assessment, assessmentList, assessmentListError, loadAssessmentList, openAssessment } from '../store/state';

const router = useRouter();
onMounted(() => loadAssessmentList());

async function open(id: string): Promise<void> {
  if (await openAssessment(id)) router.push('/eva');
}
</script>
<template>
  <div class="section-toolbar">
    <button class="button" type="button" @click="loadAssessmentList"><Icon name="RefreshCw" :size="16" />Обновить список</button>
  </div>

  <div class="report-cards" v-if="assessment">
    <section class="panel">
      <Icon name="FileText" :size="34" />
      <h2>Открытый расчёт {{ assessment.id }}</h2>
      <p>{{ MODE_LABEL[assessment.request.mode] }} · ВКД {{ fmtUtc(assessment.request.eva_start) }}, {{ assessment.request.duration_hours }} ч · создан {{ fmtUtc(assessment.created_at) }} · алгоритм {{ assessment.algorithm_version }}</p>
      <div class="toolbar-actions">
        <a class="button primary" :href="exportUrl(assessment.id)"><Icon name="Download" :size="17" />Скачать zip</a>
        <a class="button" :href="jsonUrl(assessment.id)" target="_blank" rel="noopener"><Icon name="Braces" :size="17" />JSON результата</a>
      </div>
    </section>
    <section class="panel">
      <Icon name="Package" :size="34" />
      <h2>Что внутри выгрузки</h2>
      <p>result.json совпадает с ответом API; report.md читаемый отчёт с параметрами, отсечением, оценками, рекомендацией, ссылками на записи и версиями данных; sources.json и timeline.csv для машинной обработки.</p>
    </section>
  </div>

  <p v-if="assessmentListError" class="form-error panel inline-error"><Icon name="TriangleAlert" :size="16" />{{ assessmentListError }}</p>

  <section class="panel table-panel">
    <table>
      <thead><tr><th>Создан (UTC)</th><th>Режим</th><th>Начало ВКД (UTC)</th><th>ID</th><th></th></tr></thead>
      <tbody>
        <tr v-for="item in assessmentList" :key="item.id" :class="{ preferred: assessment?.id === item.id }">
          <td class="mono">{{ fmtUtc(item.created_at, true) }}</td>
          <td>{{ MODE_LABEL[item.mode] || item.mode }}</td>
          <td class="mono">{{ fmtUtc(item.eva_start) }}</td>
          <td class="mono">{{ item.id }}</td>
          <td>
            <div class="row-actions">
              <button class="button small" type="button" @click="open(item.id)"><Icon name="ExternalLink" :size="13" />Открыть</button>
              <a class="button small" :href="exportUrl(item.id)"><Icon name="Download" :size="13" />zip</a>
              <a class="button small" :href="jsonUrl(item.id)" target="_blank" rel="noopener"><Icon name="Braces" :size="13" />JSON</a>
            </div>
          </td>
        </tr>
      </tbody>
    </table>
    <div v-if="!assessmentList.length && !assessmentListError" class="empty-state"><Icon name="FileText" :size="34" /><h3>Расчётов на сервере нет</h3></div>
  </section>
</template>
