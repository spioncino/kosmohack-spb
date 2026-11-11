import { degreesLat, degreesLong, eciToGeodetic, gstime, json2satrec, propagate, type SatRec } from 'satellite.js';
import type { Omm, TrajectorySample } from '../api/client';
import { ms } from './format';

export interface GeoPoint {
  lat: number;
  lon: number;
  alt: number;
  // Скорость в км/с относительно инерциальной системы.
  speed: number | null;
}

// SGP4 по OMM CelesTrak; ошибка разбора возвращает null.
export function satrecFromOmm(omm: Omm): SatRec | null {
  try {
    return json2satrec(omm as never);
  } catch {
    return null;
  }
}

export function propagateGeo(satrec: SatRec, at: Date): GeoPoint | null {
  try {
    const pv = propagate(satrec, at);
    if (!pv || !pv.position || typeof pv.position === 'boolean') return null;
    const geo = eciToGeodetic(pv.position, gstime(at));
    const velocity = pv.velocity && typeof pv.velocity !== 'boolean' ? pv.velocity : null;
    return {
      lat: degreesLat(geo.latitude),
      lon: degreesLong(geo.longitude),
      alt: geo.height,
      speed: velocity ? Math.hypot(velocity.x, velocity.y, velocity.z) : null,
    };
  } catch {
    return null;
  }
}

// Период обращения по средней скорости движения (оборотов в сутки).
export function periodMinutes(omm: Omm): number | null {
  const motion = Number(omm.MEAN_MOTION);
  return Number.isFinite(motion) && motion > 0 ? 1440 / motion : null;
}

export function nearestSample(samples: TrajectorySample[], at: number): TrajectorySample | null {
  if (!samples.length) return null;
  let lo = 0;
  let hi = samples.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (ms(samples[mid].t) <= at) lo = mid;
    else hi = mid;
  }
  return Math.abs(ms(samples[lo].t) - at) <= Math.abs(ms(samples[hi].t) - at) ? samples[lo] : samples[hi];
}

// Наибольший разрыв между соседними сэмплами, внутри которого положение интерполируется.
export const INTERPOLATION_GAP_MS = 6 * 60000;

// Положение на момент `at` по двум соседним сэмплам трассы: широта и высота линейно, долгота по короткой дуге
// через линию перемены дат. Вне ряда сэмплов и на разрыве больше INTERPOLATION_GAP_MS положения нет.
export function interpolatePoint(samples: TrajectorySample[], at: number): { lat: number; lon: number; alt: number; index: number } | null {
  if (samples.length < 2 || at < ms(samples[0].t) || at > ms(samples[samples.length - 1].t)) return null;
  let lo = 0;
  let hi = samples.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (ms(samples[mid].t) <= at) lo = mid;
    else hi = mid;
  }
  const a = samples[lo];
  const b = samples[hi];
  const span = ms(b.t) - ms(a.t);
  if (span <= 0 || span > INTERPOLATION_GAP_MS) return null;
  const k = (at - ms(a.t)) / span;
  let dlon = b.lon_deg - a.lon_deg;
  if (dlon > 180) dlon -= 360;
  if (dlon < -180) dlon += 360;
  let lon = a.lon_deg + dlon * k;
  if (lon > 180) lon -= 360;
  if (lon < -180) lon += 360;
  return { lat: a.lat_deg + (b.lat_deg - a.lat_deg) * k, lon, alt: a.alt_km + (b.alt_km - a.alt_km) * k, index: k < 0.5 ? lo : hi };
}

export function sampleIndexAt(samples: TrajectorySample[], at: number): number {
  const sample = nearestSample(samples, at);
  return sample ? samples.indexOf(sample) : -1;
}

const EARTH_RADIUS_KM = 6371.0;

// Скорость по соседним сэмплам: длина дуги на сфере радиуса (R + h) плюс изменение высоты.
export function speedFromSamples(samples: TrajectorySample[], index: number): number | null {
  const a = samples[index - 1] || samples[index];
  const b = samples[index + 1] || samples[index];
  if (a === b) return null;
  const dt = (ms(b.t) - ms(a.t)) / 1000;
  if (dt <= 0) return null;
  const toRad = Math.PI / 180;
  const phi1 = a.lat_deg * toRad;
  const phi2 = b.lat_deg * toRad;
  const dphi = phi2 - phi1;
  const dlambda = (b.lon_deg - a.lon_deg) * toRad;
  const h = Math.sin(dphi / 2) ** 2 + Math.cos(phi1) * Math.cos(phi2) * Math.sin(dlambda / 2) ** 2;
  const angle = 2 * Math.atan2(Math.sqrt(h), Math.sqrt(1 - h));
  const radius = EARTH_RADIUS_KM + (a.alt_km + b.alt_km) / 2;
  const arc = angle * radius;
  const dh = b.alt_km - a.alt_km;
  return Math.hypot(arc, dh) / dt;
}

// Трасса по SGP4 на равномерной сетке; sunlit, mlat и ЮАА в этом варианте неизвестны.
export function sgp4Track(satrec: SatRec, start: number, end: number, stepMinutes = 1): TrajectorySample[] {
  const samples: TrajectorySample[] = [];
  for (let t = start; t <= end; t += stepMinutes * 60000) {
    const point = propagateGeo(satrec, new Date(t));
    if (!point) continue;
    samples.push({
      t: new Date(t).toISOString().replace(/\.\d{3}Z$/, 'Z'),
      lat_deg: point.lat,
      lon_deg: point.lon,
      alt_km: point.alt,
      sunlit: true,
      mlat_deg: null,
      in_saa: false,
      level: 'unknown',
    });
  }
  return samples;
}
