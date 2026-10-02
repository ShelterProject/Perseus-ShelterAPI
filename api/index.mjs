// Perseus-ShelterAPI -- AWS Lambda (Function URL), pengganti Cloudflare
// Worker. Dipindah dari Cloudflare karena socket TCP mentah (nodejs_compat)
// maupun Hyperdrive dari runtime isolate Workers ke Aiven gak reliable
// (macet/gagal berulang). Lambda jalan di runtime Node biasa (bukan
// isolate V8), jadi koneksi Postgres-nya sama persis caranya kayak
// pipeline Python di GitHub Actions yang sudah terbukti jalan.
//
// Endpoint:
//   GET /predictions/weather?lat=&lon=&scope=city|province&radius_km=
//   GET /predictions/earthquake?lat=&lon=&scope=city|province&radius_km=
//   GET /predictions/forest-fire?lat=&lon=&scope=city|province&radius_km=
//   GET /hazard-zones/flood?lat=&lon=&scope=city|province&radius_km=
//   GET /hazard-zones/landslide?lat=&lon=&scope=city|province&radius_km=
//
// Akses dibatasi satu identifier: header `X-App-Key` harus cocok sama
// env var APP_KEY (di-generate sekali, disimpan sebagai secret -- bukan
// hardcode). Request yang gak punya/gak cocok key-nya ditolak di baris
// PALING AWAL, sebelum kode lain jalan apalagi connect ke Postgres --
// biar DDoS/scan acak gak pernah sampai bikin koneksi DB (Aiven free
// tier cuma kasih sedikit slot koneksi bersamaan) ataupun kena biaya
// compute yang berarti.
//
// Database di-hit LANGSUNG tiap request (gak ada cache) -- datanya harus
// selalu yang paling baru. Yang melindungi Aiven dari kebanjiran koneksi
// kalau lagi ramai/diserang bukan cache, tapi Reserved Concurrency di
// Lambda (di-set waktu deploy, lihat deploy-api.yml) -- itu bikin jumlah
// eksekusi Lambda yang jalan BERSAMAAN gak pernah lebih dari limit
// koneksi Aiven, dan kelebihannya di-throttle gratis oleh Lambda sendiri
// sebelum sempat jalan sama sekali.

import postgres from "postgres";

let sql; // reused lintas invocation dalam container Lambda yang sama (warm start)

function connect() {
  if (sql) return sql;
  sql = postgres({
    host: process.env.PG_HOST,
    port: Number(process.env.PG_PORT),
    username: process.env.PG_USER,
    password: process.env.PG_PASSWORD,
    database: process.env.PG_DATABASE,
    ssl: { ca: process.env.PG_CA_CERT },
    max: 1,
    fetch_types: false,
  });
  return sql;
}

const PREDICTION_TABLES = new Set([
  "prediction_weather",
  "prediction_earthquake",
  "prediction_forest_fire",
  "hazard_index_flood",
  "hazard_index_landslide",
]);

function jsonResponse(statusCode, data) {
  return {
    statusCode,
    headers: { "content-type": "application/json; charset=utf-8" },
    body: JSON.stringify(data),
  };
}

function parseQuery(params) {
  const lat = parseFloat(params.lat ?? "");
  const lon = parseFloat(params.lon ?? "");
  if (Number.isNaN(lat) || Number.isNaN(lon)) return null;
  const scope = params.scope ?? "province";
  const radiusKm = params.radius_km !== undefined ? parseFloat(params.radius_km) : null;
  return { lat, lon, scope, radiusKm };
}

// Rumus haversine persis sama dengan pipeline/lib/geo.py -- resolve region
// terdekat/radius dan join ke tabel prediksi dalam SATU query.
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

async function handlePredictionQuery(db, query, table, fkCol, orderCol) {
  const q = parseQuery(query);
  if (!q) return jsonResponse(400, { error: "lat & lon wajib diisi" });

  const rows = await db.unsafe(
    `${RESOLVE_REGIONS_CTE}
     SELECT p.*, r.name AS region_name, r.province_code AS region_province_code
     FROM resolved r
     LEFT JOIN ${table} p ON p.${fkCol} = r.id
     ORDER BY r.id, p.${orderCol}`,
    [q.lat, q.lon, q.radiusKm, q.scope]
  );

  if (rows.length === 0) {
    return jsonResponse(404, { error: "Tidak ada wilayah ditemukan di sekitar koordinat ini" });
  }

  const resolvedRegions = new Map();
  const data = [];
  for (const row of rows) {
    const regionId = row[fkCol] ?? row.id;
    resolvedRegions.set(regionId, {
      id: regionId,
      name: row.region_name,
      province_code: row.region_province_code,
    });
    const { region_name, region_province_code, ...prediction } = row;
    if (Object.values(prediction).some((v) => v !== null)) data.push(prediction);
  }

  return jsonResponse(200, {
    resolved_regions: [...resolvedRegions.values()],
    scope: q.radiusKm !== null ? "radius" : q.scope,
    count: data.length,
    data,
  });
}

const ROUTES = {
  "/predictions/weather": ["prediction_weather", "region_id", "date"],
  "/predictions/earthquake": ["prediction_earthquake", "nearest_region_id", "date"],
  "/predictions/forest-fire": ["prediction_forest_fire", "region_id", "date"],
  "/hazard-zones/flood": ["hazard_index_flood", "region_id", "region_id"],
  "/hazard-zones/landslide": ["hazard_index_landslide", "region_id", "region_id"],
};

export const handler = async (event) => {
  const headers = event.headers ?? {};
  const providedKey = headers["x-app-key"] ?? headers["X-App-Key"];
  if (!providedKey || providedKey !== process.env.APP_KEY) {
    // Ditolak di sini -- belum ada koneksi DB yang dibuka sama sekali.
    return jsonResponse(401, { error: "Unauthorized" });
  }

  const method = event.requestContext?.http?.method ?? "GET";
  if (method !== "GET") {
    return jsonResponse(405, { error: "Method not allowed" });
  }

  const path = event.rawPath ?? "/";
  const route = ROUTES[path];
  if (!route) {
    return jsonResponse(404, { error: "Not found" });
  }

  const db = connect();
  const [table, fkCol, orderCol] = route;
  if (!PREDICTION_TABLES.has(table)) {
    return jsonResponse(404, { error: "Not found" });
  }

  try {
    return await handlePredictionQuery(db, event.queryStringParameters ?? {}, table, fkCol, orderCol);
  } catch (e) {
    console.error(e);
    return jsonResponse(500, { error: "Internal error" });
  }
};
