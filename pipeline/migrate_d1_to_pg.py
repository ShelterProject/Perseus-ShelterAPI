"""Migrasi satu kali: pindahin semua data yang sudah kesimpen di Cloudflare
D1 ke Postgres (Aiven), sebelum D1 ditinggalkan sepenuhnya.

Kenapa perlu ini: waktu masih pakai D1, bootstrap sempat jalan sebagian
(sekitar 161rb baris ke-tulis sebelum kena limit harian D1) -- daripada
kerja ulang narik dari Open-Meteo/USGS/FIRMS dari nol, data yang sudah ada
dipindah langsung ke Postgres.

Urutan tabel PENTING (harus sesuai urutan foreign key: referensi wilayah
dulu, baru raw data, baru hasil prediksi) -- jangan diacak.

Jalankan SEKALI setelah db/schema.sql diterapkan ke Postgres, SEBELUM
menjalankan pipeline fetch_*/train_* versi Postgres yang baru. Butuh 2 set
kredensial sekaligus: yang lama (CLOUDFLARE_ACCOUNT_ID/API_TOKEN/
D1_DATABASE_ID, buat baca) dan yang baru (PG_*, buat tulis).
"""
import sys

from lib.d1 import fetch_all as d1_fetch_all
from lib.pg import bulk_insert as pg_bulk_insert
from lib.pg import execute as pg_execute

# Tabel yang id-nya di-insert eksplisit (biar konsisten sama FK dari
# raw_weather/prediction_* yang sudah mereferensikan id lama) -- sequence
# SERIAL-nya perlu di-reset manual setelah migrasi, kalau tidak insert
# berikutnya (lewat seed_regions.py/fetch_earthquake.py yang gak nyebut id)
# bisa tabrakan sama id yang barusan dimasukkan manual.
TABLES_WITH_EXPLICIT_ID = ["regions", "seismic_zones", "raw_earthquake_events", "raw_hotspots"]

# (nama tabel, daftar kolom persis urutan di skema, conflict_target)
TABLES = [
    ("provinces", ["code", "name"], "code"),
    ("regions", ["id", "bps_code", "province_code", "name", "centroid_lat", "centroid_lon"], "id"),
    ("seismic_zones", ["id", "name", "min_lat", "max_lat", "min_lon", "max_lon"], "id"),
    ("raw_weather",
     ["region_id", "date", "temp_min", "temp_max", "temp_mean", "humidity_avg",
      "rainfall", "sunshine_hours", "wind_max", "wind_avg", "fetched_at"],
     "region_id, date"),
    ("raw_earthquake_events",
     ["id", "seismic_zone_id", "event_time", "lat", "lon", "magnitude", "depth_km", "usgs_id", "fetched_at"],
     "id"),
    ("raw_hotspots",
     ["id", "region_id", "acq_date", "lat", "lon", "brightness", "confidence", "frp", "fetched_at"],
     "id"),
    ("prediction_weather",
     ["region_id", "date", "temp_min", "temp_max", "temp_mean", "humidity_avg",
      "rainfall", "sunshine_hours", "wind_max", "wind_avg", "condition", "generated_at"],
     "region_id, date"),
    ("prediction_earthquake",
     ["seismic_zone_id", "date", "lat_avg", "lat_min", "lat_max", "lon_avg", "lon_min", "lon_max",
      "magnitude_avg", "depth_min_km", "depth_max_km", "nearest_region_id", "generated_at"],
     "seismic_zone_id, date"),
    ("prediction_forest_fire", ["region_id", "date", "confidence", "generated_at"], "region_id, date"),
    ("hazard_index_flood", ["region_id", "hazard_index", "source_layer", "fetched_at"], "region_id"),
    ("hazard_index_landslide", ["region_id", "hazard_index", "source_layer", "fetched_at"], "region_id"),
]

CHUNK = 5000  # baris per batch baca dari D1, biar gak sekali tarik semua ke memori


def migrate_table(table: str, columns: list[str], conflict_target: str) -> int:
    col_list = ", ".join(columns)
    offset = 0
    total = 0
    while True:
        page = d1_fetch_all(f"SELECT {col_list} FROM {table} LIMIT {CHUNK} OFFSET {offset}")
        if not page:
            break
        rows = [tuple(row[c] for c in columns) for row in page]
        n = pg_bulk_insert(table, columns, rows, on_conflict="ignore", conflict_target=conflict_target)
        total += n
        offset += CHUNK
        print(f"  {table}: {offset} baris dibaca dari D1, {total} baris ditulis ke Postgres")
        if len(page) < CHUNK:
            break
    return total


def main():
    for table, columns, conflict_target in TABLES:
        print(f"Migrasi {table} ...")
        try:
            n = migrate_table(table, columns, conflict_target)
            print(f"{table}: selesai, {n} baris.\n")
        except Exception as e:
            print(f"{table}: GAGAL ({e}) -- lanjut ke tabel berikutnya, cek manual nanti.", file=sys.stderr)

    print("Reset sequence SERIAL buat tabel yang id-nya di-insert eksplisit ...")
    for table in TABLES_WITH_EXPLICIT_ID:
        pg_execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {table}), 1))"
        )
        print(f"  {table}: sequence direset.")

    print("Migrasi selesai. Verifikasi jumlah baris di kedua sisi sebelum menghapus database D1.")


if __name__ == "__main__":
    main()
