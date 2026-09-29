"""Tarik data gempa dari USGS Earthquake Catalog (gantiin BMKG Data Online)
per zona seismik, window rolling 5 tahun, simpan ke raw_earthquake_events.

USGS membatasi maksimal 20.000 event per response -- kalau kena limit itu
di satu zona pada satu window, pecah request per tahun (dilakukan otomatis
di bawah, biar aman meski kemungkinan besar tiap zona jauh di bawah limit).
"""
import sys
from datetime import date, timedelta

from lib.pg import bulk_insert, fetch_all
from lib.http import get_with_retry

USGS_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
MIN_MAGNITUDE = 1.0


def fetch_zone_events(zone: dict, start_date: str, end_date: str) -> list[dict]:
    params = {
        "format": "geojson",
        "starttime": start_date,
        "endtime": end_date,
        "minlatitude": zone["min_lat"],
        "maxlatitude": zone["max_lat"],
        "minlongitude": zone["min_lon"],
        "maxlongitude": zone["max_lon"],
        "minmagnitude": MIN_MAGNITUDE,
    }
    resp = get_with_retry(USGS_URL, params=params, expect_json=True)
    return resp.json().get("features", [])


def main():
    zones = fetch_all("SELECT id, name, min_lat, max_lat, min_lon, max_lon FROM seismic_zones")
    if not zones:
        print("Tabel seismic_zones kosong -- jalankan seed_regions.py dulu.", file=sys.stderr)
        sys.exit(1)

    # Skip di awal per zona: cuma tarik dari event terakhir yang sudah ada
    # di DB, bukan window 5 tahun penuh tiap bulan.
    latest_per_zone = {
        r["seismic_zone_id"]: r["max_date"]
        for r in fetch_all(
            "SELECT seismic_zone_id, MAX(event_time) AS max_date FROM raw_earthquake_events GROUP BY seismic_zone_id"
        )
    }

    end = date.today()
    bootstrap_start = end - timedelta(days=5 * 365)
    fetched_at = end.isoformat()

    total = 0
    skipped = 0
    for zone in zones:
        last_date = latest_per_zone.get(zone["id"])
        if last_date:
            last_date = last_date if isinstance(last_date, date) else date.fromisoformat(last_date)
            start = last_date + timedelta(days=1)
        else:
            start = bootstrap_start
        if start > end:
            skipped += 1
            print(f"Zona '{zone['name']}': sudah up-to-date, skip")
            continue

        # USGS strict soal starttime > endtime; per tahun biar respons gak raksasa
        rows = []
        cursor = start
        while cursor < end:
            chunk_end = min(cursor + timedelta(days=365), end)
            features = fetch_zone_events(zone, cursor.isoformat(), chunk_end.isoformat())
            for f in features:
                props = f["properties"]
                lon, lat, depth = f["geometry"]["coordinates"]
                usgs_id = f["id"]
                event_time = date.fromtimestamp(props["time"] / 1000).isoformat()
                rows.append((
                    zone["id"], event_time, lat, lon,
                    props["mag"], depth, usgs_id, fetched_at,
                ))
            cursor = chunk_end

        n = bulk_insert(
            "raw_earthquake_events",
            ["seismic_zone_id", "event_time", "lat", "lon", "magnitude", "depth_km", "usgs_id", "fetched_at"],
            rows,
            on_conflict="replace", conflict_target="usgs_id",  # dedup otomatis antar fetch bulanan
        )
        total += n
        print(f"Zona '{zone['name']}': {n} event")

    print(f"Selesai. Total baris raw_earthquake_events: {total} ({skipped} zona sudah up-to-date, di-skip)")


if __name__ == "__main__":
    main()
