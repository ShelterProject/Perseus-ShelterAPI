// Perseus-ShelterAPI Worker
//
// Satu-satunya server yang dikenal Mobile App. Semua sumber data eksternal
// (Open-Meteo, USGS, FIRMS, InaRISK) cuma dipanggil oleh pipeline Python di
// GitHub Actions -- Worker ini murni baca dari Postgres, konek TCP
// langsung (nodejs_compat) persis kayak pipeline Python, TANPA Hyperdrive.
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

import postgres from "postgres";
import { Region, nearestRegion, regionsWithinRadius } from "./geo";

export interface Env {
  PG_HOST: string;
  PG_PORT: string;
  PG_USER: string;
  PG_PASSWORD: string;
  PG_DATABASE: string;
  PG_CA_CERT: string;
}

function connect(env: Env): postgres.Sql {
  return postgres({
    host: env.PG_HOST,
    port: Number(env.PG_PORT),
    username: env.PG_USER,
    password: env.PG_PASSWORD,
    database: env.PG_DATABASE,
    // `ssl: { ca }` bikin handshake TLS gagal berulang di socket Workers
    // (custom CA pinning gak kesupport penuh), tiap percobaan connect()
    // dihitung subrequest -> cepat ngebentur limit "too many subrequests".
    // `require` tetap terenkripsi (TLS), cuma gak verifikasi CA Aiven --
    // cukup buat koneksi internal Worker->DB yang kredensialnya sendiri
    // rahasia (beda kasus sama lib/pg.py yang jalan di GitHub Actions,
    // di sana verify-ca gak masalah karena bukan socket Workers).
    ssl: "require",
    max: 1,
  });
}

// Whitelist tabel yang boleh di-query lewat handlePredictionQuery -- nama
// tabel di sini gak pernah dari input user, cuma dipilih lewat routing
// switch-case di bawah, jadi aman diinterpolasi ke SQL.
type PredictionTable =
  | "prediction_weather"
  | "prediction_forest_fire"
  | "hazard_index_flood"
  | "hazard_index_landslide";

async function loadRegions(sql: postgres.Sql): Promise<Region[]> {
  return sql<Region[]>`
    SELECT id, province_code, bps_code, name, centroid_lat, centroid_lon FROM regions
  `;
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
  sql: postgres.Sql,
  url: URL,
  table: PredictionTable,
  orderCol: string = "date"
): Promise<Response> {
  const { lat, lon, scope, radiusKm } = parseQuery(url);
  if (Number.isNaN(lat) || Number.isNaN(lon)) {
    return jsonResponse({ error: "lat & lon wajib diisi" }, 400);
  }

  const regions = await loadRegions(sql);
  const targets = resolveTargetRegions(regions, lat, lon, scope, radiusKm);
  if (targets.length === 0) {
    return jsonResponse({ error: "Tidak ada wilayah ditemukan di sekitar koordinat ini" }, 404);
  }

  const ids = targets.map((r) => r.id);
  // Nama tabel & kolom di sini dari whitelist internal (PredictionTable +
  // orderCol yang di-hardcode per pemanggilan), bukan dari input user --
  // aman diinterpolasi. `ids` tetap lewat parameter binding (sql(ids)).
  const rows = await sql.unsafe(
    `SELECT * FROM ${table} WHERE region_id = ANY($1) ORDER BY region_id, ${orderCol}`,
    [ids]
  );

  return jsonResponse({
    resolved_regions: targets.map((r) => ({ id: r.id, name: r.name, province_code: r.province_code })),
    scope: radiusKm !== null ? "radius" : scope,
    count: rows.length,
    data: rows,
  });
}

async function handleEarthquakeQuery(sql: postgres.Sql, url: URL): Promise<Response> {
  const { lat, lon, scope, radiusKm } = parseQuery(url);
  if (Number.isNaN(lat) || Number.isNaN(lon)) {
    return jsonResponse({ error: "lat & lon wajib diisi" }, 400);
  }

  const regions = await loadRegions(sql);
  const targets = resolveTargetRegions(regions, lat, lon, scope, radiusKm);
  if (targets.length === 0) {
    return jsonResponse({ error: "Tidak ada wilayah ditemukan di sekitar koordinat ini" }, 404);
  }

  // Gempa disimpan per zona seismik, bukan per kabupaten -- ambil lewat
  // nearest_region_id yang sudah di-resolve pipeline training.
  const ids = targets.map((r) => r.id);
  const rows = await sql`
    SELECT * FROM prediction_earthquake WHERE nearest_region_id = ANY(${ids}) ORDER BY date
  `;

  return jsonResponse({
    resolved_regions: targets.map((r) => ({ id: r.id, name: r.name, province_code: r.province_code })),
    scope: radiusKm !== null ? "radius" : scope,
    count: rows.length,
    data: rows,
  });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (request.method !== "GET") {
      return jsonResponse({ error: "Method not allowed" }, 405);
    }

    const sql = connect(env);

    try {
      switch (url.pathname) {
        case "/predictions/weather":
          return await handlePredictionQuery(sql, url, "prediction_weather");
        case "/predictions/earthquake":
          return await handleEarthquakeQuery(sql, url);
        case "/predictions/forest-fire":
          return await handlePredictionQuery(sql, url, "prediction_forest_fire");
        case "/hazard-zones/flood":
          return await handlePredictionQuery(sql, url, "hazard_index_flood", "region_id");
        case "/hazard-zones/landslide":
          return await handlePredictionQuery(sql, url, "hazard_index_landslide", "region_id");
        default:
          return jsonResponse({ error: "Not found" }, 404);
      }
    } catch (e) {
      // SEMENTARA: tampilin detail error di response langsung (bukan cuma
      // di Workers Logs) buat debugging awal, karena dashboard logs susah
      // diakses. Ini kebuka ke publik, jadi WAJIB dicabut lagi begitu
      // Worker-nya kekonfirmasi jalan normal -- jangan dibiarkan di prod.
      const err = e as Error;
      return jsonResponse({ error: "Internal error", message: err.message, stack: err.stack }, 500);
    } finally {
      // Gak ada Hyperdrive yang pool koneksi buat kita -- tutup eksplisit
      // tiap request selesai, biar gak numpuk koneksi ke Postgres.
      await sql.end({ timeout: 0 });
    }
  },
};
