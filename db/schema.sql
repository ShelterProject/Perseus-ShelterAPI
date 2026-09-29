-- Perseus-ShelterAPI — skema PostgreSQL (Aiven), pengganti d1/schema.sql.
--
-- Pindah dari Cloudflare D1 karena D1 free tier punya limit KERAS jumlah
-- baris ditulis/hari (bukan soal storage) yang kena pas bootstrap histori
-- 5 tahun untuk 514 kabupaten/kota. Postgres gak punya limit semacam itu,
-- cuma dibatasi storage & koneksi.
--
-- Algoritma & alur data TETAP SAMA seperti sebelumnya (lihat komentar di
-- tiap tabel) -- cuma dialect SQL & tempat host database yang berubah.

-- ============================================================
-- REFERENSI WILAYAH
-- ============================================================

CREATE TABLE IF NOT EXISTS provinces (
    code        TEXT PRIMARY KEY,
    name        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS regions (
    id              SERIAL PRIMARY KEY,
    province_code   TEXT NOT NULL REFERENCES provinces(code),
    bps_code        TEXT UNIQUE NOT NULL,
    name            TEXT NOT NULL,
    centroid_lat    DOUBLE PRECISION NOT NULL,
    centroid_lon    DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_regions_province ON regions(province_code);

CREATE TABLE IF NOT EXISTS seismic_zones (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    min_lat     DOUBLE PRECISION NOT NULL,
    max_lat     DOUBLE PRECISION NOT NULL,
    min_lon     DOUBLE PRECISION NOT NULL,
    max_lon     DOUBLE PRECISION NOT NULL
);

-- ============================================================
-- RAW DATA (histori rolling 5 tahun, input training)
-- ============================================================

CREATE TABLE IF NOT EXISTS raw_weather (
    region_id       INTEGER NOT NULL REFERENCES regions(id),
    date            DATE NOT NULL,
    temp_min        DOUBLE PRECISION,
    temp_max        DOUBLE PRECISION,
    temp_mean       DOUBLE PRECISION,
    humidity_avg    DOUBLE PRECISION,
    rainfall        DOUBLE PRECISION,
    sunshine_hours  DOUBLE PRECISION,
    wind_max        DOUBLE PRECISION,
    wind_avg        DOUBLE PRECISION,
    fetched_at      DATE NOT NULL,
    PRIMARY KEY (region_id, date)
);

CREATE TABLE IF NOT EXISTS raw_earthquake_events (
    id              SERIAL PRIMARY KEY,
    seismic_zone_id INTEGER NOT NULL REFERENCES seismic_zones(id),
    event_time      DATE NOT NULL,
    lat             DOUBLE PRECISION NOT NULL,
    lon             DOUBLE PRECISION NOT NULL,
    magnitude       DOUBLE PRECISION NOT NULL,
    depth_km        DOUBLE PRECISION NOT NULL,
    usgs_id         TEXT UNIQUE,
    fetched_at      DATE NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quake_zone_time ON raw_earthquake_events(seismic_zone_id, event_time);

CREATE TABLE IF NOT EXISTS raw_hotspots (
    id              SERIAL PRIMARY KEY,
    region_id       INTEGER REFERENCES regions(id),
    acq_date        DATE NOT NULL,
    lat             DOUBLE PRECISION NOT NULL,
    lon             DOUBLE PRECISION NOT NULL,
    brightness      DOUBLE PRECISION,
    confidence      DOUBLE PRECISION,
    frp             DOUBLE PRECISION,
    fetched_at      DATE NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hotspot_region_date ON raw_hotspots(region_id, acq_date);
CREATE UNIQUE INDEX IF NOT EXISTS idx_hotspot_unique ON raw_hotspots(region_id, acq_date, lat, lon);

-- ============================================================
-- HASIL PREDIKSI (dibaca API)
-- ============================================================

CREATE TABLE IF NOT EXISTS prediction_weather (
    region_id       INTEGER NOT NULL REFERENCES regions(id),
    date            DATE NOT NULL,
    temp_min        DOUBLE PRECISION,
    temp_max        DOUBLE PRECISION,
    temp_mean       DOUBLE PRECISION,
    humidity_avg    DOUBLE PRECISION,
    rainfall        DOUBLE PRECISION,
    sunshine_hours  DOUBLE PRECISION,
    wind_max        DOUBLE PRECISION,
    wind_avg        DOUBLE PRECISION,
    condition       TEXT,
    generated_at    DATE NOT NULL,
    PRIMARY KEY (region_id, date)
);

CREATE TABLE IF NOT EXISTS prediction_earthquake (
    seismic_zone_id INTEGER NOT NULL REFERENCES seismic_zones(id),
    date            DATE NOT NULL,
    lat_avg         DOUBLE PRECISION,
    lat_min         DOUBLE PRECISION,
    lat_max         DOUBLE PRECISION,
    lon_avg         DOUBLE PRECISION,
    lon_min         DOUBLE PRECISION,
    lon_max         DOUBLE PRECISION,
    magnitude_avg   DOUBLE PRECISION,
    depth_min_km    DOUBLE PRECISION,
    depth_max_km    DOUBLE PRECISION,
    nearest_region_id INTEGER REFERENCES regions(id),
    generated_at    DATE NOT NULL,
    PRIMARY KEY (seismic_zone_id, date)
);

CREATE TABLE IF NOT EXISTS prediction_forest_fire (
    region_id       INTEGER NOT NULL REFERENCES regions(id),
    date            DATE NOT NULL,
    confidence      DOUBLE PRECISION,
    generated_at    DATE NOT NULL,
    PRIMARY KEY (region_id, date)
);

CREATE TABLE IF NOT EXISTS hazard_index_flood (
    region_id       INTEGER PRIMARY KEY REFERENCES regions(id),
    hazard_index    DOUBLE PRECISION NOT NULL,
    source_layer    TEXT NOT NULL,
    fetched_at      DATE NOT NULL
);

CREATE TABLE IF NOT EXISTS hazard_index_landslide (
    region_id       INTEGER PRIMARY KEY REFERENCES regions(id),
    hazard_index    DOUBLE PRECISION NOT NULL,
    source_layer    TEXT NOT NULL,
    fetched_at      DATE NOT NULL
);

-- ============================================================
-- OBSERVABILITY
-- ============================================================

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id              SERIAL PRIMARY KEY,
    data_type       TEXT NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL,
    finished_at     TIMESTAMPTZ,
    status          TEXT NOT NULL,
    rows_written    INTEGER,
    error_message   TEXT
);
