<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import type { Level, WindowScore } from '../api/client';
import Icon from '../components/Icon.vue';
import PlainList from '../components/PlainList.vue';
import PlainParts from '../components/PlainParts.vue';
import PlanChangeBanner from '../components/PlanChangeBanner.vue';
import ProgressSteps from '../components/ProgressSteps.vue';
import ReasonView from '../components/ReasonView.vue';
import StaleBanner from '../components/StaleBanner.vue';
import Segments from '../components/Segments.vue';
import Term from '../components/Term.vue';
import { fmtTime, fmtUtc, ms, percent } from '../lib/format';
import { MODE_LABEL, lineLabel } from '../lib/labels';
import { periodCaption } from '../lib/period';
import { HOW_CHOSEN, minutesOf, plainExclusions, plainExplanation } from '../lib/plain';
import { hoursText, withDuration } from '../lib/plan';
import { formDifferences, formFromRequest, staleNotice } from '../lib/requestForm';
import { hoursByLine, maxHours, unmeasuredNote } from '../lib/score';
import { assessment, job, jobError, jobRunning, notify, runAssessment, scores, setSelectedTime, userJobRunning } from '../store/state';

const router = useRouter();
const result = computed(() => assessment.value);
const duration = ref<number>(result.value?.request.duration_hours ?? 6);
// Поле длительности следует за открытым расчётом: после открытия другого расчёта оно показывает его длительность.
watch(
  () => result.value?.request.duration_hours,
  (hours) => {
    if (hours !== undefined) duration.value = hours;
  },
);

// Длительность в поле не совпадает с показанным расчётом или идёт новый расчёт пользователя (D85): баннер над
// результатом и приглушённые рекомендация и таблица окон; сравнение в терминах формы /eva (lib/requestForm.ts).
const durationChanges = computed(() => (result.value ? formDifferences({ ...formFromRequest(result.value.request), duration: duration.value }, result.value.request) : []));
const stale = computed(() => (result.value ? staleNotice(durationChanges.value, userJobRunning.value, 'Пересчитать') : null));

// Полнота окон при неизмеренном потоке это покрытие уровнями из прогноза: фраза над таблицей.
const completenessNote = computed(() => unmeasuredNote(scores.value));

const lines = computed(() => (result.value ? Object.keys(result.value.windows[0]?.per_line || result.value.requested_window.per_line || {}) : []));

type RowStatus = 'preferred' | 'equivalent' | 'excluded' | 'candidate';

function status(score: WindowScore): RowStatus {
  const rec = result.value!.recommendation;
  if (score.excluded_reason) return 'excluded';
  if (rec.preferred && ms(rec.preferred.start) === ms(score.window.start)) return 'preferred';
  if (rec.equivalents.some((w) => ms(w.start) === ms(score.window.start))) return 'equivalent';
  return 'candidate';
}

const STATUS_LABEL: Record<RowStatus, string> = { preferred: 'рекомендовано', equivalent: 'равнозначно', excluded: 'исключено', candidate: 'кандидат' };

const rows = computed(() =>
  result.value
    ? result.value.windows.map((score) => ({
        score,
        status: status(score),
        requested: ms(score.window.start) === ms(result.value!.requested_window.window.start),
        // Причины исключения обычными словами; запись сервера видна в подсказке ячейки.
        exclusions: plainExclusions(score.excluded_reason),
      }))
    : [],
);

const minutesAt = (score: WindowScore, line: string, levels: Level[]) => minutesOf(levels.reduce((acc, level) => acc + (score.per_line[line]?.[level] || 0), 0));

// Итоговые колонки показывают минуты как максимум по линиям (сумма превышала бы длину окна); минуты каждой линии
// видны в подсказке ячейки и в колонках линий.
const HIGH: Level[] = ['high', 'critical'];
const ELEVATED: Level[] = ['elevated'];
const maxMinutes = (score: WindowScore, levels: Level[]) => minutesOf(maxHours(score.per_line, levels));
function minutesTitle(score: WindowScore, levels: Level[]): string {
  const items = hoursByLine(score.per_line, levels);
  return items.length ? `по линиям: ${items.map((item) => `${item.label} ${minutesOf(item.hours)} мин`).join(', ')}` : 'ни по одной линии';
}

// Объяснение рекомендации обычными словами; у расчётов без поля explanation показывается техническая причина.
const plain = computed(() => (result.value ? plainExplanation(result.value.recommendation) : null));
const noChoice = computed(() => !!result.value && (result.value.recommendation.insufficient || !result.value.recommendation.preferred));

// Изменение длительности это новый расчёт с тем же режимом, началом ВКД и периодом допустимого начала
// (lib/plan.ts: withDuration); результат показывается как изменение плана, а не как новая обстановка.
async function rerun(): Promise<void> {
  const base = result.value;
  if (!base) return;
  const request = withDuration(base.request, duration.value);
  if (!request) {
    notify('Период допустимого начала расчёта не проходит проверку: от 15 минут до 24 часов, начало ВКД внутри него');
    return;
  }
  const fresh = await runAssessment(request);
  if (fresh) notify(`Расчёт ${fresh.id} с длительностью ${hoursText(duration.value)} готов`);
}

function toOrbit(iso: string): void {
  setSelectedTime(iso);
  router.push('/orbit');
}
</script>
<template>
  <div class="section-toolbar">
    <div class="tab-links">
      <RouterLink to="/eva">Расчёт</RouterLink>
      <RouterLink to="/compare" class="selected">Сравнение окон <span v-if="result">{{ result.windows.length }}</span></RouterLink>
    </div>
    <div class="toolbar-actions" v-if="result">
      <label class="inline-field">Длительность
        <select v-model.number="duration">
          <option v-for="n in [1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5, 5.5, 6, 6.5, 7, 7.5, 8]" :key="n" :value="n">{{ n }} ч</option>
        </select>
      </label>
      <button class="button primary" type="button" :disabled="jobRunning || duration === result.request.duration_hours" @click="rerun"><Icon name="RotateCw" :size="16" :class="{ spinning: jobRunning }" />Пересчитать</button>
    </div>
  </div>

  <ProgressSteps :job="jobRunning ? job : null" :error="jobError" />
  <PlanChangeBanner />

  <template v-if="result">
    <StaleBanner v-if="stale" :notice="stale" :can-revert="durationChanges.length > 0" @revert="duration = result.request.duration_hours" />
    <div class="compare-summary" :class="{ 'stale-dim': stale }">
      <section class="panel compare-rec" :class="{ insufficient: result.recommendation.insufficient }">
        <span class="eyebrow">РЕКОМЕНДАЦИЯ · <Term :term="result.request.mode">{{ MODE_LABEL[result.request.mode] }}</Term></span>
        <template v-if="plain">
          <h2 class="plain-headline" :class="{ red: noChoice }"><PlainParts :parts="plain.headline.parts" /></h2>
          <PlainList :items="plain.items" />
          <details class="tech-details">
            <summary>Технические подробности: причина выбора и коды решений</summary>
            <ReasonView :reason="result.recommendation.reason" />
          </details>
        </template>
        <template v-else>
          <h2 v-if="!noChoice && result.recommendation.preferred">{{ fmtUtc(result.recommendation.preferred.start) }} – {{ fmtTime(result.recommendation.preferred.end) }} UTC</h2>
          <h2 v-else class="red">Оснований для выбора недостаточно</h2>
          <ReasonView :reason="result.recommendation.reason" />
        </template>
      </section>
      <section class="panel compare-rules">
        <span class="eyebrow">КАК ВЫБИРАЕТСЯ ОКНО</span>
        <p class="how-chosen">{{ HOW_CHOSEN }}</p>
        <p class="muted">Линии, по которым сравниваются окна: {{ lines.map(lineLabel).join(', ') || 'нет' }}. Окна одинаковой длительности {{ result.request.duration_hours }} ч с шагом 15 минут: {{ periodCaption(result.request) }}.</p>
        <details class="tech-details">
          <summary>Технические подробности: правило выбора и коды решений</summary>
          <p class="muted"><Term term="penalty">Штраф окна</Term>, допуски и исключения в записи сервера:</p>
          <ul class="rules-list">
            <li v-for="rule in result.rules" :key="rule"><Segments :text="rule" /></li>
          </ul>
        </details>
      </section>
    </div>

    <section class="panel table-panel compare-table" :class="{ 'stale-dim': stale }">
      <p v-if="completenessNote" class="rec-caveat completeness-note"><Icon name="TriangleAlert" :size="16" /><span>{{ completenessNote }}</span></p>
      <table>
        <thead>
          <tr>
            <th><Term term="start_period">Начало (UTC)</Term></th>
            <th>Конец (UTC)</th>
            <th><Term term="danger_minutes">Опасные минуты</Term><small>максимум по линиям</small></th>
            <th><Term term="elevated_background">Повышенный фон, мин</Term><small>максимум по линиям</small></th>
            <th v-for="line in lines" :key="line">{{ lineLabel(line) }}<small>опасные / фон, мин</small></th>
            <th><Term term="completeness">Полнота</Term></th>
            <th><Term term="equivalent_windows">Статус</Term></th>
            <th><Term term="window_exclusion">Причина исключения</Term></th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.score.window.start" :class="[row.status, { requested: row.requested }]">
            <td class="mono">{{ fmtUtc(row.score.window.start) }}<small v-if="row.requested"> · запрошено</small></td>
            <td class="mono">{{ fmtUtc(row.score.window.end) }}</td>
            <td class="mono hours-cell" :title="minutesTitle(row.score, HIGH)">{{ maxMinutes(row.score, HIGH) }}</td>
            <td class="mono hours-cell" :title="minutesTitle(row.score, ELEVATED)">{{ maxMinutes(row.score, ELEVATED) }}</td>
            <td v-for="line in lines" :key="line" class="mono">{{ minutesAt(row.score, line, HIGH) }} / {{ minutesAt(row.score, line, ELEVATED) }}</td>
            <td class="mono">{{ percent(row.score.completeness) }}</td>
            <td><span class="row-status" :class="row.status">{{ STATUS_LABEL[row.status] }}</span></td>
            <td class="reason" :title="row.score.excluded_reason || undefined"><span v-for="(text, i) in row.exclusions" :key="i" class="plain-reason">{{ text }}</span></td>
            <td><button class="text-btn" type="button" @click="toOrbit(row.score.window.start)">На орбите <Icon name="ArrowUpRight" :size="13" /></button></td>
          </tr>
        </tbody>
      </table>
    </section>
  </template>

  <div v-else class="panel empty-state">
    <Icon name="Columns3" :size="35" />
    <h2>Нет расчёта для сравнения</h2>
    <p>Запустите расчёт в планировании: окна одинаковой длительности с началом в периоде допустимого начала будут сравнены между собой.</p>
    <RouterLink to="/eva" class="button">К планированию</RouterLink>
  </div>
</template>
