<script setup lang="ts">
import { computed } from 'vue';
import { levelTerm } from '../lib/glossary';
import { parseReason } from '../lib/reason';
import Icon from './Icon.vue';
import Segments from './Segments.vue';
import Term from './Term.vue';

// Причина рекомендации: первая часть заголовком, остальные пунктами, отрезки high и critical метками
// «04:36–04:50 · протоны · critical». Текст сервера только перестраивается (lib/reason.ts: parseReason).
const props = defineProps<{ reason: string }>();
const parsed = computed(() => parseReason(props.reason));
</script>
<template>
  <div class="reason" :class="{ caveat: parsed.caveat }">
    <p class="reason-head">
      <Icon v-if="parsed.caveat" name="TriangleAlert" :size="16" />
      <span><strong><Segments :parts="parsed.headline" /></strong><template v-if="parsed.note"><span class="reason-note">: <Segments :parts="parsed.note" /></span></template></span>
    </p>
    <ul v-if="parsed.items.length" class="reason-list">
      <li v-for="(item, i) in parsed.items" :key="i">
        <template v-if="item.label"><span class="reason-label"><Segments :parts="item.label" /></span>: </template>
        <span v-if="item.ranges.length" class="reason-ranges">
          <span v-for="(r, j) in item.ranges" :key="j" class="reason-chip" :class="`level-${r.level}`">
            <span class="mono">{{ r.start }}–{{ r.end }}</span><template v-if="r.lineWord"> · {{ r.lineWord }}</template> · <Term :term="levelTerm(r.level)">{{ r.level }}</Term>
          </span>
          <span v-if="item.more" class="reason-more">и ещё {{ item.more }}</span>
        </span>
        <Segments v-else :parts="item.body" />
      </li>
    </ul>
  </div>
</template>
