<script setup lang="ts">
import { computed } from 'vue';
import { segments, type ReasonSegment } from '../lib/reason';

// Текст сервера для чтения: коды линий русскими подписями, коды решений D… ссылками на журнал решений.
const props = defineProps<{ text?: string | null; parts?: ReasonSegment[] | null }>();
const list = computed(() => props.parts ?? (props.text ? segments(props.text) : []));
const isCode = (seg: ReasonSegment): seg is { code: string; route: string } => 'code' in seg;
</script>
<template><template v-for="(seg, i) in list" :key="i"><RouterLink v-if="isCode(seg)" :to="seg.route" class="decision-link" :title="`Решение ${seg.code} в журнале решений`">{{ seg.code }}</RouterLink><template v-else>{{ seg.text }}</template></template></template>
