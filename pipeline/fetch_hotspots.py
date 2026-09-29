"""Tarik data titik panas dari NASA FIRMS (gantiin SiPongi KLHK manual),
window rolling 5 tahun, simpan ke raw_hotspots -- tiap titik di-resolve ke
kabupaten/kota terdekat pakai haversine (teknik yang sama dipakai notebook
Shelter_ML buat cari kota terdekat dari titik gempa/kebakaran).

VIIRS_SNPP_SP (Standard/Science Product) dipakai buat histori -- sumber ini
day_range-nya maks 5 hari per request, jadi 5 tahun ditarik per potongan
5 hari (~365 request). FIRMS_MAP_KEY wajib di-set lewat environment
variable / GitHub secret, JANGAN di-hardcode (repo ini public).
"""
import csv
import io
import os
import sys
import time
from datetime import date, timedelta

from lib.d1 import bulk_insert, fetch_all
from lib.geo import nearest
from lib.http import get_with_retry

FIRMS_BASE = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
SOURCE = "VIIRS_SNPP_SP"
DAY_RANGE = 5  # maksimal yang diizinkan FIRMS untuk sumber *_SP

# Bounding box seluruh Indonesia (west, south, east, north)
INDONESIA_BBOX = "94.5,-11.5,141.5,6.5"

# Peta confidence kategorikal (dipakai sumber *_SP) ke skala numerik 0-100,
# biar konsisten dengan confidence numerik yang dipakai training di notebook lama.
CONFIDENCE_MAP = {"l": 30, "n": 60, "h": 90}


def firms_map_key() -> str:
    key = os.environ.get("FIRMS_MAP_KEY")
    if not key:
        print("FIRMS_MAP_KEY belum di-set di environment.", file=sys.stderr)
        sys.exit(1)
    return key


def fetch_chunk(map_key: str, start: date) -> list[dict]:
    url = f"{FIRMS_BASE}/{map_key}/{SOURCE}/{INDONESIA_BBOX}/{DAY_RANGE}/{start.isoformat()}"
    resp = get_with_retry(url)
    text = resp.text.strip()
    if not text or text.startswith("Invalid") or text.startswith("Error"):
        if text:
            print(f"FIRMS warning @ {start}: {text[:200]}", file=sys.stderr)
        return []
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)


def main():
    map_key = firms_map_key()
    regions = fetch_all("SELECT id, centroid_lat, centroid_lon FROM regions")
    if not regions:
        print("Tabel regions kosong -- jalankan seed_regions.py dulu.", file=sys.stderr)
        sys.exit(1)

    end = date.today()
    start = end - timedelta(days=5 * 365)
    fetched_at = end.isoformat()

    total = 0
    cursor = start
    while cursor < end:
        records = fetch_chunk(map_key, cursor)
        rows = []
        for r in records:
            try:
                lat, lon = float(r["latitude"]), float(r["longitude"])
            except (KeyError, ValueError):
                continue
            region, dist_km = nearest(lat, lon, regions)
            if region is None or dist_km > 75:
                continue  # terlalu jauh dari kabupaten manapun, skip

            raw_conf = r.get("confidence", "").strip().lower()
            confidence = CONFIDENCE_MAP.get(raw_conf)
            if confidence is None:
                try:
                    confidence = float(raw_conf)  # sumber NRT numerik langsung
                except ValueError:
                    confidence = None

            rows.append((
                region["id"], r["acq_date"], lat, lon,
                float(r.get("bright_ti4") or 0) or None,
                confidence,
                float(r.get("frp") or 0) or None,
                fetched_at,
            ))

        n = bulk_insert(
            "raw_hotspots",
            ["region_id", "acq_date", "lat", "lon", "brightness", "confidence", "frp", "fetched_at"],
            rows,
            on_conflict="replace",  # unique index (region_id, acq_date, lat, lon) -> dedup antar fetch bulanan
        )
        total += n
        cursor += timedelta(days=DAY_RANGE)
        time.sleep(0.3)  # jaga di bawah limit 5000 transaksi/10 menit

    print(f"Selesai. Total baris raw_hotspots: {total}")


if __name__ == "__main__":
    main()
