-- Shelter Cloud v2 — Cloudflare D1 schema
--
-- Algoritma tetap sama seperti Shelter_ML (SARIMAX untuk cuaca & gempa,
-- Decision Tree Regressor per kabupaten untuk karhutla). Yang berubah cuma
-- sumber data (Open-Meteo, USGS, NASA FIRMS, InaRISK BNPB menggantikan BMKG
-- Data Online / SiPongi / KML manual) dan cakupan (seluruh Indonesia,
-- bukan cuma Sumatera Utara).
--
-- Alur: GitHub Actions bulanan -> tabel raw_* (histori 5 tahun rolling)
--       -> training -> tabel prediction_* / hazard_index_*
--       -> Cloudflare Workers API baca dari sini -> Mobile App

-- ============================================================
-- REFERENSI WILAYAH
-- ============================================================

-- Provinsi & kabupaten/kota, diisi sekali dari layer `batas_administrasi`
-- InaRISK BNPB (kode KDPPUM/KDPBPS = kode BPS resmi). Jarang berubah,
-- di-refresh manual/tahunan, bukan bagian job bulanan.
CREATE TABLE IF NOT EXISTS provinces (
    code        TEXT PRIMARY KEY,   -- kode provinsi BPS, contoh '12' = Sumatera Utara
    name        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS regions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    province_code   TEXT NOT NULL REFERENCES provinces(code),
    bps_code        TEXT UNIQUE,        -- kode kabupaten/kota BPS
    name            TEXT NOT NULL,      -- contoh 'Kabupaten Asahan'
    centroid_lat    REAL NOT NULL,      -- dipakai buat fetch cuaca/karhutla per titik & haversine
    centroid_lon    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_regions_province ON regions(province_code);

-- Zona seismik (Sumatera, Jawa-Nusa Tenggara, Sulawesi, Maluku-Papua, dst).
-- Gempa dipecah per zona (bukan 1 model nasional) karena tiap zona proses
-- geologisnya beda -- lihat diskusi sebelumnya.
CREATE TABLE IF NOT EXISTS seismic_zones (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    min_lat     REAL NOT NULL,
    max_lat     REAL NOT NULL,
    min_lon     REAL NOT NULL,
    max_lon     REAL NOT NULL
);
-- Index terpisah (bukan inline UNIQUE di kolom) supaya tetap bisa
-- diterapkan lewat `IF NOT EXISTS` ke database yang tabelnya sudah
-- kadung dibuat tanpa constraint ini di deploy pertama.
CREATE UNIQUE INDEX IF NOT EXISTS idx_seismic_zones_name ON seismic_zones(name);

-- ============================================================
-- RAW DATA (histori rolling 5 tahun, dipakai sebagai input training)
-- ============================================================

-- Sumber: Open-Meteo (forecast + historical archive), 1 baris per
-- region per hari. RH_avg & ff_avg dihitung manual dari data hourly
-- (Open-Meteo daily API gak nyediain langsung).
CREATE TABLE IF NOT EXISTS raw_weather (
    region_id       INTEGER NOT NULL REFERENCES regions(id),
    date            TEXT NOT NULL,      -- yyyy-MM-dd
    temp_min        REAL,               -- Tn
    temp_max        REAL,               -- Tx
    temp_mean       REAL,               -- Tavg
    humidity_avg    REAL,               -- RH_avg
    rainfall        REAL,               -- RR
    sunshine_hours  REAL,               -- ss
    wind_max        REAL,               -- ff_x
    wind_avg        REAL,               -- ff_avg
    fetched_at      TEXT NOT NULL,
    PRIMARY KEY (region_id, date)
);

-- Sumber: USGS Earthquake Catalog API, bounding box per seismic_zone.
-- 1 baris = 1 event gempa asli (bukan agregat harian) -- agregasi
-- harian (avg/min/max lintang-bujur-magnitudo-kedalaman) dihitung di
-- tahap training, persis logika notebook lama.
CREATE TABLE IF NOT EXISTS raw_earthquake_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    seismic_zone_id INTEGER NOT NULL REFERENCES seismic_zones(id),
    event_time      TEXT NOT NULL,      -- ISO8601
    lat             REAL NOT NULL,
    lon             REAL NOT NULL,
    magnitude       REAL NOT NULL,
    depth_km        REAL NOT NULL,
    usgs_id         TEXT UNIQUE,        -- dedup antar fetch bulanan
    fetched_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quake_zone_time ON raw_earthquake_events(seismic_zone_id, event_time);

-- Sumber: NASA FIRMS (VIIRS SP untuk histori, NRT untuk data terbaru).
-- 1 baris = 1 titik panas asli.
CREATE TABLE IF NOT EXISTS raw_hotspots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    region_id       INTEGER REFERENCES regions(id),   -- hasil resolve haversine ke kabupaten terdekat
    acq_date        TEXT NOT NULL,
    lat             REAL NOT NULL,
    lon             REAL NOT NULL,
    brightness      REAL,               -- bright_ti4
    confidence      REAL,               -- dinormalisasi ke 0-100 (FIRMS SP pakai n/l/h, di-mapping saat insert)
    frp             REAL,
    fetched_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hotspot_region_date ON raw_hotspots(region_id, acq_date);
-- Dedup antar fetch bulanan (window 5 tahun rolling selalu overlap dengan
-- histori yang sudah ada) -- INSERT OR REPLACE butuh target constraint ini.
CREATE UNIQUE INDEX IF NOT EXISTS idx_hotspot_unique ON raw_hotspots(region_id, acq_date, lat, lon);

-- ============================================================
-- HASIL PREDIKSI (output training, yang dibaca API)
-- ============================================================

-- Hasil SARIMAX cuaca, forecast 365 hari ke depan per region.
-- Di-replace penuh tiap bulan (delete generation lama, insert baru).
CREATE TABLE IF NOT EXISTS prediction_weather (
    region_id       INTEGER NOT NULL REFERENCES regions(id),
    date            TEXT NOT NULL,
    temp_min        REAL,
    temp_max        REAL,
    temp_mean       REAL,
    humidity_avg    REAL,
    rainfall        REAL,
    sunshine_hours  REAL,
    wind_max        REAL,
    wind_avg        REAL,
    condition       TEXT,               -- diturunkan dari kombinasi rainfall/cloud, sama logika lama
    generated_at    TEXT NOT NULL,
    PRIMARY KEY (region_id, date)
);

-- Hasil SARIMAX gempa, forecast 365 hari ke depan per zona seismik,
-- plus nearest_region_id hasil haversine (pengganti fungsi `terdekat()`
-- di notebook lama).
CREATE TABLE IF NOT EXISTS prediction_earthquake (
    seismic_zone_id INTEGER NOT NULL REFERENCES seismic_zones(id),
    date            TEXT NOT NULL,
    lat_avg         REAL,
    lat_min         REAL,
    lat_max         REAL,
    lon_avg         REAL,
    lon_min         REAL,
    lon_max         REAL,
    magnitude_avg   REAL,
    depth_min_km    REAL,
    depth_max_km    REAL,
    nearest_region_id INTEGER REFERENCES regions(id),
    generated_at    TEXT NOT NULL,
    PRIMARY KEY (seismic_zone_id, date)
);

-- Hasil Decision Tree Regressor per kabupaten/kota (~514 model),
-- forecast 365 hari, fitur = prediction_weather + jarak haversine.
CREATE TABLE IF NOT EXISTS prediction_forest_fire (
    region_id       INTEGER NOT NULL REFERENCES regions(id),
    date            TEXT NOT NULL,
    confidence      REAL,               -- 0-100, keluaran model
    generated_at    TEXT NOT NULL,
    PRIMARY KEY (region_id, date)
);

-- Indeks bahaya banjir & longsor dari InaRISK BNPB (ImageServer,
-- di-sample per region_id pakai operasi `identify`). Statis/jarang
-- berubah -- di-refresh terpisah dari job bulanan ML (mis. tahunan),
-- bukan time-series jadi gak ada kolom `date`.
CREATE TABLE IF NOT EXISTS hazard_index_flood (
    region_id       INTEGER PRIMARY KEY REFERENCES regions(id),
    hazard_index    REAL NOT NULL,      -- skala 0-1 dari InaRISK
    source_layer    TEXT NOT NULL,      -- nama layer InaRISK yang dipakai, buat audit
    fetched_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hazard_index_landslide (
    region_id       INTEGER PRIMARY KEY REFERENCES regions(id),
    hazard_index    REAL NOT NULL,
    source_layer    TEXT NOT NULL,
    fetched_at      TEXT NOT NULL
);

-- ============================================================
-- OBSERVABILITY
-- ============================================================

-- Log tiap kali job GitHub Actions jalan -- buat debug kalau salah
-- satu sumber data gagal/berubah format di tengah jalan.
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    data_type       TEXT NOT NULL,      -- 'weather' | 'earthquake' | 'forest_fire' | 'hazard_flood' | 'hazard_landslide'
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    status          TEXT NOT NULL,      -- 'running' | 'success' | 'failed'
    rows_written    INTEGER,
    error_message   TEXT
);
