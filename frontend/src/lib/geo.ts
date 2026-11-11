import { feature } from 'topojson-client';
import world from 'world-atlas/land-110m.json';

// Контуры суши Natural Earth 1:110m из world-atlas (локальный JSON, без сетевых запросов).
export type Ring = number[][];

let cache: Ring[] | null = null;

export function landRings(): Ring[] {
  if (cache) return cache;
  const land: any = feature(world as any, (world as any).objects.land);
  const geometries = land.type === 'FeatureCollection' ? land.features.map((f: any) => f.geometry) : [land.geometry];
  const polygons: number[][][][] = geometries.flatMap((g: any) => (g.type === 'MultiPolygon' ? g.coordinates : [g.coordinates]));
  cache = polygons.flatMap((poly) => poly);
  return cache;
}
