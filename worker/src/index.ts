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
//
// Resolusi region (nearest/radius) dan ambil data prediksi digabung jadi
// SATU query ber-JOIN (CTE), bukan dua query terpisah -- Aiven free tier
// cuma kasih kuota koneksi terbatas, dan tiap query baru lewat socket
// Workers kena biaya "subrequest" sendiri, jadi makin sedikit round-trip
// per request makin aman dari limit itu.

import postgres from "postgres";

export interface Env {
  // Binding Hyperdrive (lihat wrangler.toml.example) -- Worker gak pernah
  // konek TCP mentah ke Aiven sendiri, cuma ke Hyperdrive yang sudah
  // di-pool & di-manage Cloudflare. connectionString-nya siap pakai
  // langsung sebagai argumen `postgres()`.
  HYPERDRIVE: { connectionString: string };
}

function connect(env: Env): postgres.Sql {
  return postgres(env.HYPERDRIVE.connectionString, {
    max: 5,
    fetch_types: false,
  });
}

// Whitelist tabel yang boleh di-query -- nama tabel di sini gak pernah dari
// input user, cuma dipilih lewat routing switch-case di bawah, jadi aman
// diinterpolasi ke SQL.
type PredictionTable =
  | "prediction_weather"
  | "prediction_earthquake"
  | "prediction_forest_fire"
  | "hazard_index_flood"
  | "hazard_index_landslide";

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

interface ParsedQuery {
  lat: number;
  lon: number;
  scope: string;
  radiusKm: number | null;
}

function parseQuery(url: URL): ParsedQuery | null {
  const lat = parseFloat(url.searchParams.get("lat") ?? "");
  const lon = parseFloat(url.searchParams.get("lon") ?? "");
  if (Number.isNaN(lat) || Number.isNaN(lon)) return null;
  const scope = url.searchParams.get("scope") ?? "province";
  const radiusParam = url.searchParams.get("radius_km");
  const radiusKm = radiusParam !== null ? parseFloat(radiusParam) : null;
  return { lat, lon, scope, radiusKm };
}

// CTE bersama: hitung jarak haversine tiap region ke titik user (persis
// rumus yang sama dengan pipeline/lib/geo.py), cari region terdekat, lalu
// resolve daftar region target sesuai scope/radius -- semua di satu
// statement SQL.
const RESOLVE_REGIONS_CTE = `
  WITH distances AS (
    SELECT id, province_code, name,
      (6371 * 2 * asin(sqrt(
        sin(radians($1 - centroid_lat) / 2) ^ 2
        + cos(radians(centroid_lat)) * cos(radians($1))
          * sin(radians($2 - centroid_lon) / 2) ^ 2
      ))) AS distance_km
    FROM regions
  ),
  nearest AS (
    SELECT province_code FROM distances ORDER BY distance_km ASC LIMIT 1
  ),
  resolved AS (
    SELECT d.id, d.name, d.province_code, d.distance_km
    FROM distances d, nearest n
    WHERE
      ($3::float8 IS NOT NULL AND d.distance_km <= $3::float8)
      OR ($3::float8 IS NULL AND $4 = 'city' AND d.distance_km = (SELECT MIN(distance_km) FROM distances))
      OR ($3::float8 IS NULL AND $4 != 'city' AND d.province_code = n.province_code)
  )
`;

async function handlePredictionQuery(
  sql: postgres.Sql,
  url: URL,
  table: PredictionTable,
  fkCol: string,
  orderCol: string
): Promise<Response> {
  const q = parseQuery(url);
  if (!q) return jsonResponse({ error: "lat & lon wajib diisi" }, 400);

  const rows = await sql.unsafe(
    `${RESOLVE_REGIONS_CTE}
     SELECT p.*, r.name AS region_name, r.province_code AS region_province_code
     FROM resolved r
     LEFT JOIN ${table} p ON p.${fkCol} = r.id
     ORDER BY r.id, p.${orderCol}`,
    [q.lat, q.lon, q.radiusKm, q.scope]
  );

  if (rows.length === 0) {
    return jsonResponse({ error: "Tidak ada wilayah ditemukan di sekitar koordinat ini" }, 404);
  }

  const resolvedRegions = new Map<number, { id: number; name: string; province_code: string }>();
  const data: unknown[] = [];
  for (const row of rows as any[]) {
    resolvedRegions.set(row[fkCol] ?? row.id, {
      id: row[fkCol] ?? row.id,
      name: row.region_name,
      province_code: row.region_province_code,
    });
    const { region_name, region_province_code, ...prediction } = row;
    if (Object.values(prediction).some((v) => v !== null)) data.push(prediction);
  }

  return jsonResponse({
    resolved_regions: [...resolvedRegions.values()],
    scope: q.radiusKm !== null ? "radius" : q.scope,
    count: data.length,
    data,
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
          return await handlePredictionQuery(sql, url, "prediction_weather", "region_id", "date");
        case "/predictions/earthquake":
          // Gempa disimpan per zona seismik, bukan per kabupaten -- kolom
          // FK-nya nearest_region_id (hasil resolve pipeline training).
          return await handlePredictionQuery(sql, url, "prediction_earthquake", "nearest_region_id", "date");
        case "/predictions/forest-fire":
          return await handlePredictionQuery(sql, url, "prediction_forest_fire", "region_id", "date");
        case "/hazard-zones/flood":
          return await handlePredictionQuery(sql, url, "hazard_index_flood", "region_id", "region_id");
        case "/hazard-zones/landslide":
          return await handlePredictionQuery(sql, url, "hazard_index_landslide", "region_id", "region_id");
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
