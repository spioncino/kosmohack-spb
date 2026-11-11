<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue';
import * as T from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { Line2 } from 'three/addons/lines/Line2.js';
import { LineGeometry } from 'three/addons/lines/LineGeometry.js';
import { LineMaterial } from 'three/addons/lines/LineMaterial.js';
import type { TrajectorySample } from '../api/client';
import { fmtTime, ms } from '../lib/format';
import { landRings } from '../lib/geo';
import { LEVEL_COLOR } from '../lib/labels';
import { inSpan, isDanger, type Span } from '../lib/scope';

const props = defineProps<{
  samples: TrajectorySample[];
  position: { lat: number; lon: number; alt: number } | null;
  selectedTime: number;
  layers: Record<string, boolean>;
  focus?: Span | null;
  requested?: Span | null;
  preferred?: Span | null;
  leveled?: boolean;
}>();
const emit = defineEmits<{ select: []; unsupported: [] }>();

const host = ref<HTMLElement>();
let clean = () => {};

const EARTH = 2;
const EARTH_KM = 6371;

// Точка на сфере: радиус в единицах сцены, высота в километрах в реальном масштабе.
function vector(lat: number, lon: number, altKm = 0): T.Vector3 {
  const r = EARTH * (1 + altKm / EARTH_KM);
  const a = (lat * Math.PI) / 180;
  const b = (lon * Math.PI) / 180;
  return new T.Vector3(r * Math.cos(a) * Math.cos(b), r * Math.sin(a), -r * Math.cos(a) * Math.sin(b));
}

onMounted(() => {
  const el = host.value!;
  let renderer: T.WebGLRenderer;
  try {
    renderer = new T.WebGLRenderer({ antialias: true, alpha: true });
  } catch {
    emit('unsupported');
    return;
  }
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  el.appendChild(renderer.domElement);
  const scene = new T.Scene();
  scene.background = new T.Color('#080f15');
  const camera = new T.PerspectiveCamera(37, 1, 0.01, 200);
  camera.position.set(0, 1.1, 7.8);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.enablePan = false;
  controls.minDistance = 3.2;
  controls.maxDistance = 12;
  controls.target.set(0, 0, 0);
  controls.update();

  const globe = new T.Group();
  scene.add(globe);
  const earth = new T.Mesh(new T.SphereGeometry(EARTH, 72, 48), new T.MeshPhongMaterial({ color: 0x15394b, emissive: 0x03121c, shininess: 14 }));
  globe.add(earth);
  scene.add(new T.AmbientLight(0x4c758e, 1.4));
  const light = new T.DirectionalLight(0x9bc9d9, 2.2);
  light.position.set(-3, 2, 4);
  scene.add(light);
  const atmosphere = new T.Mesh(
    new T.SphereGeometry(EARTH * 1.018, 64, 48),
    new T.ShaderMaterial({
      transparent: true, side: T.BackSide, blending: T.AdditiveBlending, depthWrite: false, uniforms: {},
      vertexShader: 'varying vec3 n; varying vec3 v; void main(){ vec4 p=modelViewMatrix*vec4(position,1.); n=normalize(normalMatrix*normal);v=normalize(-p.xyz);gl_Position=projectionMatrix*p;}',
      fragmentShader: 'varying vec3 n; varying vec3 v; void main(){float i=pow(1.0-abs(dot(n,v)),3.0);gl_FragColor=vec4(.12,.52,.68,i*.48);}',
    }),
  );
  globe.add(atmosphere);

  function line(points: T.Vector3[], color: number, opacity: number, parent: T.Object3D): T.Line {
    const o = new T.Line(new T.BufferGeometry().setFromPoints(points), new T.LineBasicMaterial({ color, transparent: true, opacity }));
    parent.add(o);
    return o;
  }
  const grid = new T.Group();
  globe.add(grid);
  for (let lat = -60; lat <= 60; lat += 30) line(Array.from({ length: 181 }, (_, i) => vector(lat, i * 2, 40)), 0x447184, 0.23, grid);
  for (let lon = 0; lon < 360; lon += 30) line(Array.from({ length: 181 }, (_, i) => vector(i - 90, lon, 40)), 0x447184, 0.23, grid);
  const landGroup = new T.Group();
  globe.add(landGroup);
  for (const ring of landRings()) line(ring.map((p) => vector(p[1], p[0], 55)), 0x719693, 0.5, landGroup);

  const starGeometry = new T.BufferGeometry();
  const starCoords: number[] = [];
  for (let i = 0; i < 900; i++) {
    const r = 18 + ((i * 7919) % 100) / 10;
    const theta = i * 2.39996;
    const y = 1 - (2 * (i + 0.5)) / 900;
    const a = Math.sqrt(1 - y * y);
    starCoords.push(r * a * Math.cos(theta), r * y, r * a * Math.sin(theta));
  }
  starGeometry.setAttribute('position', new T.Float32BufferAttribute(starCoords, 3));
  scene.add(new T.Points(starGeometry, new T.PointsMaterial({ color: 0x96b5c7, size: 0.027, transparent: true, opacity: 0.65 })));

  const trackGroup = new T.Group();
  globe.add(trackGroup);
  // Яркая часть трассы это линия с толщиной в пикселях: обычная линия WebGL всегда толщиной в один пиксель.
  const futureMaterial = new LineMaterial({ vertexColors: true, linewidth: 3, transparent: true, opacity: 0.95 });
  const future = new Line2(new LineGeometry(), futureMaterial);
  const past = new T.Line(new T.BufferGeometry(), new T.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.35 }));
  trackGroup.add(future, past);
  const saaGroup = new T.Group();
  globe.add(saaGroup);
  const saaPoints = new T.Points(new T.BufferGeometry(), new T.PointsMaterial({ color: 0xe8bd70, size: 0.035, transparent: true, opacity: 0.8 }));
  saaGroup.add(saaPoints);
  const markers = new T.Group();
  globe.add(markers);
  // Запрошенное окно синее, рекомендованное зелёное: шар это выход, куб это возврат.
  const exitMarker = new T.Mesh(new T.SphereGeometry(0.04, 12, 8), new T.MeshBasicMaterial({ color: 0x79b8f0 }));
  const returnMarker = new T.Mesh(new T.BoxGeometry(0.07, 0.07, 0.07), new T.MeshBasicMaterial({ color: 0x79b8f0 }));
  const exitPreferred = new T.Mesh(new T.SphereGeometry(0.04, 12, 8), new T.MeshBasicMaterial({ color: 0x8dde90 }));
  const returnPreferred = new T.Mesh(new T.BoxGeometry(0.07, 0.07, 0.07), new T.MeshBasicMaterial({ color: 0x8dde90 }));
  markers.add(exitMarker, returnMarker, exitPreferred, returnPreferred);
  // Сэмплы уровня high и critical отмечены точками: линия WebGL всегда тонкая, проход на ней теряется.
  const dangerPoints = new T.Points(new T.BufferGeometry(), new T.PointsMaterial({ vertexColors: true, size: 0.075, transparent: true, opacity: 0.95 }));
  trackGroup.add(dangerPoints);

  const station = new T.Group();
  globe.add(station);
  station.add(new T.Mesh(new T.BoxGeometry(0.085, 0.025, 0.045), new T.MeshBasicMaterial({ color: 0xcefbdf })));
  for (const x of [-0.067, 0.067]) {
    const panel = new T.Mesh(new T.BoxGeometry(0.05, 0.13, 0.008), new T.MeshBasicMaterial({ color: 0x77e6ac }));
    panel.position.x = x;
    station.add(panel);
  }
  station.add(new T.Mesh(new T.SphereGeometry(0.045, 16, 12), new T.MeshBasicMaterial({ color: 0x9cffbb, transparent: true, opacity: 0.4 })));

  const label = document.createElement('button');
  label.className = 'iss-label';
  label.type = 'button';
  label.onclick = () => emit('select');
  el.appendChild(label);

  function colorOf(sample: TrajectorySample): T.Color {
    const c = new T.Color(props.leveled === false ? '#8fa4ad' : LEVEL_COLOR[sample.level]);
    if (props.layers.shadow && !sample.sunlit) c.multiplyScalar(0.45);
    return c;
  }

  function buildLine(target: T.Line, samples: TrajectorySample[]): void {
    const positions: number[] = [];
    const colors: number[] = [];
    for (const s of samples) {
      const v = vector(s.lat_deg, s.lon_deg, s.alt_km);
      positions.push(v.x, v.y, v.z);
      const c = colorOf(s);
      colors.push(c.r, c.g, c.b);
    }
    target.geometry.dispose();
    const geometry = new T.BufferGeometry();
    geometry.setAttribute('position', new T.Float32BufferAttribute(positions, 3));
    geometry.setAttribute('color', new T.Float32BufferAttribute(colors, 3));
    target.geometry = geometry;
    target.visible = samples.length > 1;
  }

  function place(marker: T.Mesh, sample: TrajectorySample | undefined): void {
    marker.visible = !!sample;
    if (sample) marker.position.copy(vector(sample.lat_deg, sample.lon_deg, sample.alt_km + 20));
  }

  function buildWide(samples: TrajectorySample[]): void {
    future.visible = samples.length > 1;
    if (samples.length < 2) return;
    const positions: number[] = [];
    const colors: number[] = [];
    for (const s of samples) {
      const v = vector(s.lat_deg, s.lon_deg, s.alt_km);
      positions.push(v.x, v.y, v.z);
      const c = colorOf(s);
      colors.push(c.r, c.g, c.b);
    }
    future.geometry.dispose();
    const geometry = new LineGeometry();
    geometry.setPositions(positions);
    geometry.setColors(colors);
    future.geometry = geometry;
  }

  function updateTrack(): void {
    const samples = props.samples;
    const at = props.selectedTime;
    const focus = props.focus || null;
    if (focus) {
      // Яркая линия это выбранное окно, бледная это весь период расчёта.
      buildLine(past, samples);
      buildWide(samples.filter((s) => inSpan(ms(s.t), focus)));
      (past.material as T.LineBasicMaterial).opacity = 0.22;
    } else {
      const split = samples.findIndex((s) => ms(s.t) >= at);
      buildLine(past, split < 0 ? samples : samples.slice(0, split + 1));
      buildWide(split < 0 ? [] : samples.slice(split));
      (past.material as T.LineBasicMaterial).opacity = 0.35;
    }
    const dangerPositions: number[] = [];
    const dangerColors: number[] = [];
    if (props.leveled !== false) {
      for (const s of samples) {
        if (!isDanger(s.level)) continue;
        const inside = !focus || inSpan(ms(s.t), focus);
        const v = vector(s.lat_deg, s.lon_deg, s.alt_km + 12);
        dangerPositions.push(v.x, v.y, v.z);
        const c = new T.Color(LEVEL_COLOR[s.level]).multiplyScalar(inside ? 1 : 0.35);
        dangerColors.push(c.r, c.g, c.b);
      }
    }
    dangerPoints.geometry.dispose();
    const dangerGeometry = new T.BufferGeometry();
    dangerGeometry.setAttribute('position', new T.Float32BufferAttribute(dangerPositions, 3));
    dangerGeometry.setAttribute('color', new T.Float32BufferAttribute(dangerColors, 3));
    dangerPoints.geometry = dangerGeometry;
    const saa: number[] = [];
    for (const s of samples) {
      if (!s.in_saa) continue;
      const v = vector(s.lat_deg, s.lon_deg, s.alt_km + 30);
      saa.push(v.x, v.y, v.z);
    }
    saaPoints.geometry.dispose();
    const saaGeometry = new T.BufferGeometry();
    saaGeometry.setAttribute('position', new T.Float32BufferAttribute(saa, 3));
    saaPoints.geometry = saaGeometry;
    const r = props.requested || null;
    const p = props.preferred || null;
    const same = !!r && !!p && r.start === p.start && r.end === p.end;
    place(exitMarker, r ? samples.find((s) => ms(s.t) >= r.start) : undefined);
    place(returnMarker, r ? [...samples].reverse().find((s) => ms(s.t) <= r.end) : undefined);
    place(exitPreferred, p && !same ? samples.find((s) => ms(s.t) >= p.start) : undefined);
    place(returnPreferred, p && !same ? [...samples].reverse().find((s) => ms(s.t) <= p.end) : undefined);
  }

  function updateStation(): void {
    const p = props.position;
    station.visible = !!p && !!props.layers.iss;
    if (!p) return;
    station.position.copy(vector(p.lat, p.lon, p.alt));
    station.lookAt(new T.Vector3(0, 0, 0));
    label.innerHTML = `<b>МКС <span>25544</span></b><small>${fmtTime(props.selectedTime)} UTC · ${p.lat.toFixed(1)}°, ${p.lon.toFixed(1)}° ↗</small>`;
  }

  updateTrack();
  updateStation();
  const stopTrack = watch(() => [props.samples, props.selectedTime, props.focus, props.requested, props.preferred, props.leveled, props.layers.shadow], updateTrack, { deep: false });
  const stopStation = watch(() => [props.position, props.selectedTime, props.layers.iss], updateStation, { deep: false });

  const resize = new ResizeObserver(() => {
    const { width, height } = el.getBoundingClientRect();
    if (!width || !height) return;
    renderer.setSize(width, height);
    futureMaterial.resolution.set(width, height);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  });
  resize.observe(el);

  let dragging = false;
  controls.addEventListener('start', () => (dragging = true));
  controls.addEventListener('end', () => (dragging = false));

  let frame = 0;
  const render = () => {
    frame = requestAnimationFrame(render);
    if (props.layers.follow && station.visible && !dragging) {
      const distance = camera.position.length();
      const target = station.position.clone().normalize();
      const current = camera.position.clone().normalize();
      // У противоположных направлений середина близка к нулю: тогда поворот начинается через полюс.
      const next = current.lerp(target, 0.06);
      if (next.lengthSq() < 1e-6) next.set(0, 1, 0);
      camera.position.copy(next.normalize().multiplyScalar(distance));
    }
    grid.visible = !!props.layers.grid;
    landGroup.visible = !!props.layers.countries;
    trackGroup.visible = !!props.layers.orbit;
    saaGroup.visible = !!props.layers.saa;
    markers.visible = !!props.layers.orbit;
    controls.update();
    renderer.render(scene, camera);
    if (station.visible) {
      const world = station.getWorldPosition(new T.Vector3());
      const toCamera = camera.position.clone().sub(world);
      const behind = world.clone().normalize().dot(toCamera.normalize()) < 0;
      const v = world.project(camera);
      label.style.left = `${((v.x + 1) * el.clientWidth) / 2 + 12}px`;
      label.style.top = `${((-v.y + 1) * el.clientHeight) / 2 - 35}px`;
      label.style.display = props.layers.labels && !behind ? 'block' : 'none';
    } else label.style.display = 'none';
  };
  frame = requestAnimationFrame(render);

  clean = () => {
    cancelAnimationFrame(frame);
    stopTrack();
    stopStation();
    resize.disconnect();
    controls.dispose();
    scene.traverse((o: any) => {
      o.geometry?.dispose?.();
      if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach((m: any) => m.dispose?.());
    });
    renderer.dispose();
    renderer.domElement.remove();
    label.remove();
  };
});
onUnmounted(() => clean());
</script>
<template><div ref="host" class="orbit-canvas"></div></template>
