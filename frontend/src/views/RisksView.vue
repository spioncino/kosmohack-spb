<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import Badges from '../components/Badges.vue';
import Icon from '../components/Icon.vue';
import ProvenanceList from '../components/ProvenanceList.vue';
import Segments from '../components/Segments.vue';
import Term from '../components/Term.vue';
import { fmtUtc, fmtWindow, num } from '../lib/format';
import { metricTerm } from '../lib/glossary';
import { GRADE_LABEL, LINE_ICON, LINE_ORDER, LINE_SHORT, MECHANISM_LABEL, MODE_LABEL, lineLabel } from '../lib/labels';
import { periodCaption } from '../lib/period';
import { BADGE_RULE, GEOMETRY_LINE, periodPercent, scoresByLine, unmeasuredLabel, unmeasuredText, windowLabel } from '../lib/score';
import { assessment, scores, setSelectedTime } from '../store/state';

const router = useRouter();
const result = computed(() => assessment.value);
const lines = computed(() => {
  if (!result.value) return [];
  const byName = new Map(result.value.lines.map((line) => [line.line, line]));
  const names = [...LINE_ORDER.filter((n) => byName.has(n)), ...result.value.lines.map((l) => l.line).filter((n) => !LINE_ORDER.includes(n as never))];
  return names.map((name) => byName.get(name)!);
});
const selected = ref(0);
watch(lines, () => (selected.value = Math.min(selected.value, Math.max(0, lines.value.length - 1))));
const line = computed(() => lines.value[selected.value] || null);
// Оценка ищется по имени линии: у геометрии доступа оценки нет, поэтому позиции вкладок и оценок не совпадают.
const scoreByLine = computed(() => scoresByLine(scores.value));
const score = computed(() => (line.value ? scoreByLine.value.get(line.value.line) || null : null));
const showBackground = ref(false);
const intervals = computed(() => (line.value ? line.value.intervals.filter((iv) => showBackground.value || iv.level !== 'none') : []));

// Подписи метрик по ключам LineAssessment.metrics (src/evarisk/risk/lines/*.py); щелчок по подписи открывает
// пояснение из глоссария (lib/glossary.ts: METRIC_TERM).
const METRIC_LABEL: Record<string, string> = {
  access_minutes: 'минуты доступа частиц', access_fraction: 'доля минут доступа', saa_minutes: 'минуты в ЮАА', coverage: 'покрытие данными',
  forecast_fraction: 'доля по прогнозу или персистентности', outlook_fraction: 'доля по прогнозу вероятности', persistence_minutes: 'минуты персистентности',
  unmeasured_minutes: 'минуты без измеренного потока', donki_state_minutes: 'минуты по уведомлениям DONKI', cme_forecast_minutes: 'минуты с прогнозом прихода CME',
  max_p10_pfu: 'максимум потока ≥10 MeV, pfu', max_p30_pfu: 'максимум потока ≥30 MeV, pfu', max_p100_pfu: 'максимум потока ≥100 MeV, pfu',
  max_factor: 'наибольший коэффициент MEO', max_factor_unshielded: 'коэффициент MEO без экрана Земли', shielded_minutes: 'минут под экраном Земли',
  suit_energy_joule: 'энергия частиц, пробивающих скафандр, Дж', active_showers: 'активных потоков',
  conjunctions_in_window: 'сближений в периоде', coorbiting_rows_in_report: 'строк со-орбитальных объектов в отчёте', min_range_km: 'наименьшая дистанция, км',
  report_age_hours: 'давность отчёта, ч', shield_threshold_mev: 'порог защиты, MeV', dose_proxy_usv_window: 'прокси дозы за период, мкЗв',
  dose_rate_max_usv_h: 'максимум мощности прокси дозы, мкЗв/ч', sunlit_fraction: 'доля света', longest_shadow_minutes: 'самая длинная тень, мин', starts_in_daylight: 'старт на свету',
  kp: 'Kp расчёта', cutoff_deg: 'широта обрезания, °', max_abs_mlat_deg: 'наибольшая магнитная широта трассы, °',
};
const metricLabel = (key: string) => METRIC_LABEL[key] || key;

function toOrbit(iso: string): void {
  setSelectedTime(iso);
  router.push('/orbit');
}
</script>
<template>
  <div class="section-toolbar">
    <span class="muted" v-if="result"><Term :term="result.request.mode">{{ MODE_LABEL[result.request.mode] }}</Term> · {{ periodCaption(result.request) }} · расчёт {{ result.id }}</span>
    <span class="muted" v-else>Открытого расчёта нет</span>
    <div class="toolbar-actions">
      <RouterLink to="/events" class="button"><Icon name="Activity" :size="16" />События</RouterLink>
      <RouterLink to="/eva" class="button"><Icon name="CalendarDays" :size="16" />Планирование</RouterLink>
    </div>
  </div>

  <template v-if="result && line">
    <div class="risk-tabs five">
      <button v-for="(l, i) in lines" :key="l.line" type="button" class="panel risk-tab" :class="{ chosen: selected === i }" @click="selected = i">
        <Icon :name="LINE_ICON[l.line] || 'Circle'" />
        <span>{{ LINE_SHORT[l.line] || lineLabel(l.line) }}</span>
        <template v-if="periodPercent(scoreByLine.get(l.line)) !== null">
          <strong :title="`доля периода поиска без high/critical${unmeasuredLabel(scoreByLine.get(l.line)) ? `; ${unmeasuredLabel(scoreByLine.get(l.line))}` : ''}`">{{ periodPercent(scoreByLine.get(l.line)) }} <small>%</small></strong>
        </template>
        <strong v-else class="muted">—</strong>
        <small><span class="score-pill" :class="scoreByLine.get(l.line)?.color || 'gray'" :title="scoreByLine.get(l.line) ? `${scoreByLine.get(l.line)!.reason}. ${BADGE_RULE}` : 'Геометрия доступа это условный расчёт, в оценку факторов не входит.'">{{ windowLabel(scoreByLine.get(l.line)) }}</span></small>
      </button>
    </div>

    <div class="analysis-grid">
      <section class="panel chart-card">
        <div class="card-header">
          <div>
            <h2>{{ lineLabel(line.line) }}</h2>
            <p class="muted">{{ MECHANISM_LABEL[line.mechanism] || line.mechanism }}</p>
          </div>
          <div class="badge-row">
            <Badges :availability="line.availability" />
            <Badges :grade="line.confidence.grade" />
          </div>
        </div>
        <div class="line-summary">
          <div v-if="score && score.percent !== null" class="score-pill large stacked" :class="score.color" :title="BADGE_RULE">
            <span>{{ windowLabel(score) }}<template v-if="score.window"> ({{ fmtWindow(score.window) }})</template></span>
            <span>Цвет по запрошенному окну: {{ score.reason }}</span>
            <span>Доля периода поиска без high/critical: {{ periodPercent(score) }} % · полнота периода {{ Math.floor(score.completeness * 100) }} %<template v-if="unmeasuredText(score)"> (минуты с уровнем, измерен он или взят из прогноза)</template></span>
            <span v-if="unmeasuredText(score)">Не измерено: {{ unmeasuredText(score) }}.</span>
          </div>
          <div v-else-if="line.line === GEOMETRY_LINE" class="score-pill large gray">условный расчёт геометрии доступа: в оценку факторов не входит</div>
          <div v-else class="score-pill large gray">нет данных: оценка не вычисляется, это не благоприятная обстановка</div>
          <p><strong><Term term="confidence">Уверенность</Term> {{ GRADE_LABEL[line.confidence.grade] }}.</strong> Зависит от: <Segments :text="line.confidence.depends_on" /></p>
        </div>
        <div class="card-header"><h3>Интервалы</h3><label class="inline-check"><input type="checkbox" v-model="showBackground" /> показывать фон</label></div>
        <div class="interval-list">
          <div v-for="(iv, i) in intervals" :key="i" class="interval-row">
            <div class="interval-head">
              <Badges :level="iv.level" />
              <Badges :kind="iv.kind" />
              <strong class="mono">{{ fmtUtc(iv.start) }} – {{ fmtUtc(iv.end) }}</strong>
              <button class="text-btn" type="button" @click="toOrbit(iv.start)">На орбите <Icon name="ArrowUpRight" :size="13" /></button>
            </div>
            <p v-if="iv.note"><Segments :text="iv.note" /></p>
            <details>
              <summary>Правило и источники</summary>
              <p class="rule-text"><Segments :text="iv.rule" /></p>
              <ProvenanceList :items="iv.provenance" compact />
            </details>
          </div>
          <div v-if="!intervals.length" class="empty-state small">
            <Icon name="Unplug" :size="30" />
            <p v-if="line.availability !== 'available'">Данных по линии нет ({{ line.availability }}). Отсутствие данных не означает отсутствие риска.</p>
            <p v-else>Интервалов выше фона нет; включите «показывать фон», чтобы увидеть покрытие.</p>
          </div>
        </div>
      </section>

      <section class="panel explanation">
        <h2>Значения и ограничения</h2>
        <dl class="kv" v-if="Object.keys(line.metrics).length">
          <div v-for="(value, key) in line.metrics" :key="key"><dt><Term :term="metricTerm(String(key))">{{ metricLabel(String(key)) }}</Term></dt><dd class="mono">{{ num(value, Number.isInteger(value) ? 0 : 3) }}</dd></div>
        </dl>
        <p v-else class="muted">Метрик по линии нет.</p>
        <h3>Ограничения линии</h3>
        <ul class="limits"><li v-for="l in line.limitations" :key="l"><Segments :text="l" /></li></ul>
        <h3 v-if="line.chain_ids.length">Связанные сигналы</h3>
        <p v-if="line.chain_ids.length" class="muted mono">{{ line.chain_ids.join(', ') }}</p>
        <h3>Использованные источники</h3>
        <ProvenanceList :items="result.sources_used" compact />
      </section>
    </div>

    <details class="panel limitations-card">
      <summary>Все ограничения расчёта ({{ result.limitations.length }})</summary>
      <ul><li v-for="l in result.limitations" :key="l"><Segments :text="l" /></li></ul>
    </details>
  </template>

  <div v-else class="panel empty-state">
    <Icon name="ShieldCheck" :size="38" />
    <h2>Нет открытого расчёта</h2>
    <p>Линии риска, интервалы и источники появятся после расчёта в планировании или открытия отчёта.</p>
    <RouterLink to="/eva" class="button">К планированию</RouterLink>
  </div>
</template>
