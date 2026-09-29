// Perseus-ShelterAPI Worker
//
// Satu-satunya server yang dikenal Mobile App. Semua sumber data eksternal
// (Open-Meteo, USGS, FIRMS, InaRISK) cuma dipanggil oleh pipeline Python di
// GitHub Actions -- Worker ini murni baca dari D1.
//
// Endpoint:
//   GET /predictions/weather?lat=&lon=&scope=city|province&radius_km=
//   GET /predictions/earthquake?lat=&lon=&scope=city|province&radius_km=
//   GET /predictions/forest-fire?lat=&lon=&scope=city|province&radius_km=
//   GET /hazard-zones/flood?lat=&lon=&scope=city|province&radius_km=
//   GET /hazard-zones/landslide?lat=&lon=&scope=city|province&radius_km=
//
// `scope` default 'province' (tampilan terbesar sesuai keputusan produk).
// `radius_km` kalau diisi, override scope -- ambil semua region dalam radius itu.

import { Region, nearestRegion, regionsWithinRadius } from "./geo";

export interface Env {
  DB: D1Database;
}

async function loadRegions(db: D1Database): Promise<Region[]> {
  const { results } = await db
    .prepare("SELECT id, province_code, bps_code, name, centroid_lat, centroid_lon FROM regions")
    .all<Region>();
  return results ?? [];
}

function resolveTargetRegions(
  regions: Region[],
  lat: number,
  lon: number,
  scope: string,
  radiusKm: number | null
): Region[] {
  if (radiusKm !== null) {
    return regionsWithinRadius(lat, lon, regions, radiusKm);
  }
  const nearest = nearestRegion(lat, lon, regions);
  if (!nearest) return [];
  if (scope === "city") {
    return [nearest.region];
  }
  // default: province -- semua kabupaten/kota dalam provinsi yang sama
  return regions.filter((r) => r.province_code === nearest.region.province_code);
}

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

function parseQuery(url: URL) {
  const lat = parseFloat(url.searchParams.get("lat") ?? "");
  const lon = parseFloat(url.searchParams.get("lon") ?? "");
  const scope = url.searchParams.get("scope") ?? "province";
  const radiusParam = url.searchParams.get("radius_km");
  const radiusKm = radiusParam !== null ? parseFloat(radiusParam) : null;
  return { lat, lon, scope, radiusKm };
}

async function handlePredictionQuery(
  env: Env,
  url: URL,
  table: string,
  extraOrderCol = "date"
): Promise<Response> {
  const { lat, lon, scope, radiusKm } = parseQuery(url);
  if (Number.isNaN(lat) || Number.isNaN(lon)) {
    return jsonResponse({ error: "lat & lon wajib diisi" }, 400);
  }

  const regions = await loadRegions(env.DB);
  const targets = resolveTargetRegions(regions, lat, lon, scope, radiusKm);
  if (targets.length === 0) {
    return jsonResponse({ error: "Tidak ada wilayah ditemukan di sekitar koordinat ini" }, 404);
  }

  const ids = targets.map((r) => r.id);
  const placeholders = ids.map(() => "?").join(",");
  const { results } = await env.DB
    .prepare(`SELECT * FROM ${table} WHERE region_id IN (${placeholders}) ORDER BY region_id, ${extraOrderCol}`)
    .bind(...ids)
    .all();

  return jsonResponse({
    resolved_regions: targets.map((r) => ({ id: r.id, name: r.name, province_code: r.province_code })),
    scope: radiusKm !== null ? "radius" : scope,
    count: results?.length ?? 0,
    data: results ?? [],
  });
}

async function handleEarthquakeQuery(env: Env, url: URL): Promise<Response> {
  const { lat, lon, scope, radiusKm } = parseQuery(url);
  if (Number.isNaN(lat) || Number.isNaN(lon)) {
    return jsonResponse({ error: "lat & lon wajib diisi" }, 400);
  }

  const regions = await loadRegions(env.DB);
  const targets = resolveTargetRegions(regions, lat, lon, scope, radiusKm);
  if (targets.length === 0) {
    return jsonResponse({ error: "Tidak ada wilayah ditemukan di sekitar koordinat ini" }, 404);
  }

  // Gempa disimpan per zona seismik, bukan per kabupaten -- ambil lewat
  // nearest_region_id yang sudah di-resolve pipeline training.
  const ids = targets.map((r) => r.id);
  const placeholders = ids.map(() => "?").join(",");
  const { results } = await env.DB
    .prepare(`SELECT * FROM prediction_earthquake WHERE nearest_region_id IN (${placeholders}) ORDER BY date`)
    .bind(...ids)
    .all();

  return jsonResponse({
    resolved_regions: targets.map((r) => ({ id: r.id, name: r.name, province_code: r.province_code })),
    scope: radiusKm !== null ? "radius" : scope,
    count: results?.length ?? 0,
    data: results ?? [],
  });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (request.method !== "GET") {
      return jsonResponse({ error: "Method not allowed" }, 405);
    }

    switch (url.pathname) {
      case "/predictions/weather":
        return handlePredictionQuery(env, url, "prediction_weather");
      case "/predictions/earthquake":
        return handleEarthquakeQuery(env, url);
      case "/predictions/forest-fire":
        return handlePredictionQuery(env, url, "prediction_forest_fire");
      case "/hazard-zones/flood":
        return handlePredictionQuery(env, url, "hazard_index_flood", "region_id");
      case "/hazard-zones/landslide":
        return handlePredictionQuery(env, url, "hazard_index_landslide", "region_id");
      default:
        return jsonResponse({ error: "Not found" }, 404);
    }
  },
};
