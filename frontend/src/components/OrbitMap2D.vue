<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue';
import type { TrajectorySample } from '../api/client';
import { fmtTime, ms } from '../lib/format';
import { landRings } from '../lib/geo';
import { LEVEL_COLOR } from '../lib/labels';
import { inSpan, type DangerPass, type Span } from '../lib/scope';

// Равнопромежуточная проекция на Canvas: трасса по уровню линии протонов, тень, ЮАА, окна выхода, станция.
// С фокусом трасса вне окна бледная, внутри яркая; без фокуса бледная прошедшая часть.
const props = defineProps<{
  samples: TrajectorySample[];
  position: { lat: number; lon: number; alt: number } | null;
  selectedTime: number;
  layers: Record<string, boolean>;
  focus?: Span | null;
  requested?: Span | null;
  preferred?: Span | null;
  passes?: DangerPass[];
  leveled?: boolean;
}>();
const emit = defineEmits<{ pick: [time: number]; select: [] }>();

const host = ref<HTMLElement>();
let clean = () => {};

onMounted(() => {
  const el = host.value!;
  const canvas = document.createElement('canvas');
  canvas.setAttribute('role', 'img');
  canvas.setAttribute('aria-label', 'Карта трассы станции в равнопромежуточной проекции');
  canvas.style.cursor = 'crosshair';
  el.appendChild(canvas);
  const ctx = canvas.getContext('2d')!;
  let width = 0;
  let height = 0;
  let mapX = 0;
  let mapY = 0;
  let mapW = 0;
  let mapH = 0;
  let frame = 0;
  let dirty = true;

  const project = (lat: number, lon: number) => ({ x: mapX + ((lon + 180) / 360) * mapW, y: mapY + ((90 - lat) / 180) * mapH });
  const unproject = (x: number, y: number) => ({ lon: ((x - mapX) / mapW) * 360 - 180, lat: 90 - ((y - mapY) / mapH) * 180 });

  const resize = new ResizeObserver(() => {
    const r = el.getBoundingClientRect();
    width = r.width;
    height = r.height;
    const dpr = Math.min(devicePixelRatio, 2);
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const pad = 14;
    mapW = Math.min(width - pad * 2, (height - pad * 2 - 26) * 2);
    mapH = mapW / 2;
    mapX = (width - mapW) / 2;
    mapY = (height - 26 - mapH) / 2;
    dirty = true;
  });
  resize.observe(el);

  function polyline(samples: TrajectorySample[], stroke: (s: TrajectorySample) => string, lineWidth: number, alpha: number): void {
    ctx.lineWidth = lineWidth;
    ctx.globalAlpha = alpha;
    ctx.lineCap = 'round';
    for (let i = 1; i < samples.length; i++) {
      const a = samples[i - 1];
      const b = samples[i];
      if (Math.abs(b.lon_deg - a.lon_deg) > 180) continue;
      const pa = project(a.lat_deg, a.lon_deg);
      const pb = project(b.lat_deg, b.lon_deg);
      ctx.strokeStyle = stroke(b);
      ctx.beginPath();
      ctx.moveTo(pa.x, pa.y);
      ctx.lineTo(pb.x, pb.y);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }

  function render(): void {
    frame = requestAnimationFrame(render);
    if (!dirty || !width || !height) return;
    dirty = false;
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = '#080f15';
    ctx.fillRect(0, 0, width, height);
    ctx.fillStyle = '#0c1c26';
    ctx.fillRect(mapX, mapY, mapW, mapH);

    if (props.layers.grid) {
      ctx.strokeStyle = '#6f9fa529';
      ctx.lineWidth = 0.7;
      for (let lon = -180; lon <= 180; lon += 30) {
        const p = project(0, lon);
        ctx.beginPath();
        ctx.moveTo(p.x, mapY);
        ctx.lineTo(p.x, mapY + mapH);
        ctx.stroke();
      }
      for (let lat = -60; lat <= 60; lat += 30) {
        const p = project(lat, 0);
        ctx.beginPath();
        ctx.moveTo(mapX, p.y);
        ctx.lineTo(mapX + mapW, p.y);
        ctx.stroke();
      }
    }
    if (props.layers.countries) {
      ctx.fillStyle = '#16333b';
      ctx.strokeStyle = '#72999480';
      ctx.lineWidth = 0.8;
      for (const ring of landRings()) {
        ctx.beginPath();
        ring.forEach((p, i) => {
          const q = project(p[1], p[0]);
          if (i === 0) ctx.moveTo(q.x, q.y);
          else ctx.lineTo(q.x, q.y);
        });
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
      }
    }

    const samples = props.samples;
    const at = props.selectedTime;
    const focus = props.focus || null;
    const colorOf = (s: TrajectorySample) => (props.leveled === false ? '#8fa4ad' : LEVEL_COLOR[s.level]);
    if (props.layers.orbit && samples.length > 1) {
      if (focus) {
        polyline(samples, colorOf, 1.2, 0.22);
        polyline(samples.filter((s) => inSpan(ms(s.t), focus)), colorOf, 2.6, 0.95);
      } else {
        const split = samples.findIndex((s) => ms(s.t) >= at);
        const pastSamples = split < 0 ? samples : samples.slice(0, split + 1);
        const futureSamples = split < 0 ? [] : samples.slice(split);
        polyline(pastSamples, colorOf, 2, 0.35);
        polyline(futureSamples, colorOf, 2.4, 0.95);
      }
      if (props.layers.shadow) {
        const shaded = samples.filter((s) => !s.sunlit && (!focus || inSpan(ms(s.t), focus)));
        polyline(shaded, () => '#0b0d0f', 5, 0.55);
        polyline(shaded, colorOf, 1, 0.5);
      }
    }
    if (props.layers.saa) {
      ctx.strokeStyle = '#e8bd70';
      ctx.lineWidth = 1;
      for (const s of samples) {
        if (!s.in_saa) continue;
        ctx.globalAlpha = !focus || inSpan(ms(s.t), focus) ? 1 : 0.25;
        const p = project(s.lat_deg, s.lon_deg);
        ctx.strokeRect(p.x - 2.5, p.y - 2.5, 5, 5);
      }
      ctx.globalAlpha = 1;
    }
    // Проходы уровня high и critical: утолщённая трасса с ореолом и время прохода; вне фокуса приглушены.
    if (props.layers.orbit && props.passes?.length) {
      ctx.font = '600 10px Inter, sans-serif';
      for (const pass of props.passes) {
        const inside = !focus || (pass.end >= focus.start && pass.start <= focus.end);
        const part = samples.filter((s) => ms(s.t) >= pass.start && ms(s.t) <= pass.end);
        ctx.save();
        ctx.shadowColor = LEVEL_COLOR[pass.level];
        ctx.shadowBlur = inside ? 10 : 0;
        polyline(part, () => LEVEL_COLOR[pass.level], inside ? 5 : 3, inside ? 1 : 0.4);
        ctx.restore();
        if (inside && props.layers.labels) {
          const p = project(pass.lat, pass.lon);
          const text = `${fmtTime(pass.start)}–${fmtTime(pass.end)}`;
          const tw = ctx.measureText(text).width;
          const tx = Math.min(mapX + mapW - tw - 6, Math.max(mapX + 4, p.x - tw / 2));
          const ty = pass.lat >= 0 ? p.y - 9 : p.y + 17;
          ctx.fillStyle = '#0b0d0fd9';
          ctx.fillRect(tx - 3, ty - 10, tw + 6, 13);
          ctx.fillStyle = LEVEL_COLOR[pass.level];
          ctx.fillText(text, tx, ty);
        }
      }
    }
    const marks: [Span | null | undefined, string, string, string][] = [
      [props.requested, '#79b8f0', '#a1c0da', 'запрошено'],
      [props.preferred, '#8dde90', '#8dde90', 'рекомендовано'],
    ];
    const same = !!props.requested && !!props.preferred && props.requested.start === props.preferred.start && props.requested.end === props.preferred.end;
    for (const [w, stroke, fill, word] of marks) {
      if (!w || !samples.length || (same && word === 'рекомендовано')) continue;
      const first = samples.find((s) => ms(s.t) >= w.start);
      const last = [...samples].reverse().find((s) => ms(s.t) <= w.end);
      const name = same ? 'запрошено и рекомендовано' : word;
      const below = word === 'рекомендовано';
      ctx.font = '600 10px Inter, sans-serif';
      ctx.fillStyle = fill;
      ctx.strokeStyle = stroke;
      ctx.lineWidth = 2;
      // Подпись ставится справа от метки, а у правого края карты слева от неё; под подписью тёмная подложка.
      const caption = (text: string, x: number, y: number) => {
        const tw = ctx.measureText(text).width;
        const tx = x + 9 + tw > mapX + mapW - 4 ? x - 9 - tw : x + 9;
        const ty = below ? y + 16 : y - 8;
        ctx.fillStyle = '#0b0d0fd9';
        ctx.fillRect(tx - 3, ty - 10, tw + 6, 13);
        ctx.fillStyle = fill;
        ctx.fillText(text, tx, ty);
      };
      if (first) {
        const p = project(first.lat_deg, first.lon_deg);
        ctx.beginPath();
        ctx.arc(p.x, p.y, 6, 0, Math.PI * 2);
        ctx.stroke();
        caption(`${name}: выход ${fmtTime(first.t)}`, p.x, p.y);
      }
      if (last) {
        const p = project(last.lat_deg, last.lon_deg);
        ctx.strokeRect(p.x - 6, p.y - 6, 12, 12);
        caption(`${name}: возврат ${fmtTime(last.t)}`, p.x, p.y);
      }
    }
    const pos = props.position;
    if (props.layers.iss && pos) {
      const p = project(pos.lat, pos.lon);
      ctx.save();
      ctx.shadowColor = '#67f0b788';
      ctx.shadowBlur = 12;
      ctx.strokeStyle = '#b3f3d7';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(p.x - 13, p.y);
      ctx.lineTo(p.x + 13, p.y);
      ctx.moveTo(p.x, p.y - 7);
      ctx.lineTo(p.x, p.y + 7);
      ctx.stroke();
      ctx.fillStyle = '#74dba6';
      ctx.fillRect(p.x - 13, p.y - 6, 6, 12);
      ctx.fillRect(p.x + 7, p.y - 6, 6, 12);
      ctx.shadowBlur = 0;
      if (props.layers.labels) {
        const lx = p.x + 120 > mapX + mapW ? p.x - 128 : p.x + 18;
        ctx.fillStyle = '#14252dea';
        ctx.strokeStyle = '#527a6c';
        ctx.lineWidth = 0.7;
        ctx.beginPath();
        ctx.roundRect(lx, p.y - 28, 112, 36, 6);
        ctx.fill();
        ctx.stroke();
        ctx.fillStyle = '#b5e6ce';
        ctx.font = '600 11px Inter, sans-serif';
        ctx.fillText('МКС', lx + 9, p.y - 13);
        ctx.fillStyle = '#769d90';
        ctx.font = '9px Inter, sans-serif';
        ctx.fillText(`${fmtTime(at)} UTC · ${pos.lat.toFixed(1)}°, ${pos.lon.toFixed(1)}°`, lx + 9, p.y + 1);
      }
      ctx.restore();
    }

    ctx.strokeStyle = '#5294a86b';
    ctx.lineWidth = 1;
    ctx.strokeRect(mapX, mapY, mapW, mapH);
    ctx.font = '9px Inter, sans-serif';
    ctx.fillStyle = '#7d909d';
    for (const lon of [-180, -90, 0, 90, 180]) {
      const p = project(-90, lon);
      ctx.fillText(`${lon}°`, p.x - 8, mapY + mapH + 12);
    }
    // Легенда справа под картой, чтобы не перекрываться панелью координат слева.
    let lx = mapX + mapW * 0.44;
    const ly = mapY + mapH + 24;
    const legend: [string, string][] = [['#5bd3bb', 'фон'], ['#e8bd70', 'повышенный'], ['#e87470', 'высокий'], ['#c58af0', 'критический'], ['#5c646c', 'нет уровня']];
    for (const [color, text] of legend) {
      ctx.fillStyle = color;
      ctx.fillRect(lx, ly - 7, 9, 9);
      ctx.fillStyle = '#8fa4ad';
      ctx.fillText(text, lx + 12, ly);
      lx += ctx.measureText(text).width + 26;
    }
    ctx.fillStyle = '#0b0d0f';
    ctx.fillRect(lx, ly - 7, 9, 9);
    ctx.strokeStyle = '#5bd3bb';
    ctx.strokeRect(lx, ly - 7, 9, 9);
    ctx.fillStyle = '#8fa4ad';
    ctx.fillText('тень', lx + 12, ly);
    lx += ctx.measureText('тень').width + 26;
    ctx.strokeStyle = '#e8bd70';
    ctx.strokeRect(lx, ly - 7, 9, 9);
    ctx.fillText('ЮАА', lx + 12, ly);
  }

  let downAt = 0;
  const down = () => (downAt = Date.now());
  const up = (e: PointerEvent) => {
    if (Date.now() - downAt > 300) return;
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    if (x < mapX || x > mapX + mapW || y < mapY || y > mapY + mapH) return;
    const { lat, lon } = unproject(x, y);
    const pos = props.position;
    if (pos) {
      const p = project(pos.lat, pos.lon);
      if (Math.hypot(p.x - x, p.y - y) < 18) {
        emit('select');
        return;
      }
    }
    let best: TrajectorySample | null = null;
    let bestD = Infinity;
    for (const s of props.samples) {
      const d = Math.hypot(s.lat_deg - lat, (s.lon_deg - lon) * Math.cos((lat * Math.PI) / 180));
      if (d < bestD) {
        bestD = d;
        best = s;
      }
    }
    if (best && bestD < 6) emit('pick', ms(best.t));
  };
  canvas.addEventListener('pointerdown', down);
  canvas.addEventListener('pointerup', up);
  const stop = watch(() => [props.samples, props.selectedTime, props.position, props.focus, props.requested, props.preferred, props.passes, props.leveled, { ...props.layers }], () => (dirty = true), { deep: true });
  frame = requestAnimationFrame(render);
  clean = () => {
    cancelAnimationFrame(frame);
    stop();
    resize.disconnect();
    canvas.removeEventListener('pointerdown', down);
    canvas.removeEventListener('pointerup', up);
    canvas.remove();
  };
});
onUnmounted(() => clean());
</script>
<template><div ref="host" class="orbit-canvas map2d"></div></template>
