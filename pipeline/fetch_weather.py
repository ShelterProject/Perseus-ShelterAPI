"""Tarik data cuaca dari Open-Meteo (gantiin BMKG Data Online) untuk tiap
kabupaten/kota, window rolling 5 tahun ke belakang, simpan ke raw_weather.

RH_avg & ff_avg gak tersedia langsung di endpoint daily Open-Meteo, jadi
dihitung manual dari data hourly (relative_humidity_2m, wind_speed_10m).
"""
import sys
import time
from datetime import date, timedelta
from statistics import mean

import requests

from lib.d1 import bulk_insert, fetch_all

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
DAILY_VARS = "temperature_2m_min,temperature_2m_max,temperature_2m_mean,precipitation_sum,sunshine_duration,wind_speed_10m_max"
HOURLY_VARS = "relative_humidity_2m,wind_speed_10m"


def fetch_region_weather(region: dict, start_date: str, end_date: str) -> list[tuple]:
    params = {
        "latitude": region["centroid_lat"],
        "longitude": region["centroid_lon"],
        "start_date": start_date,
        "end_date": end_date,
        "daily": DAILY_VARS,
        "hourly": HOURLY_VARS,
        "timezone": "Asia/Jakarta",
    }
    resp = requests.get(ARCHIVE_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    daily = data.get("daily", {})
    hourly = data.get("hourly", {})
    dates = daily.get("time", [])

    # rata-rata RH & wind per hari dari 24 nilai hourly
    hourly_by_date: dict[str, dict[str, list[float]]] = {}
    for i, ts in enumerate(hourly.get("time", [])):
        d = ts.split("T")[0]
        bucket = hourly_by_date.setdefault(d, {"rh": [], "wind": []})
        rh = hourly["relative_humidity_2m"][i]
        wind = hourly["wind_speed_10m"][i]
        if rh is not None:
            bucket["rh"].append(rh)
        if wind is not None:
            bucket["wind"].append(wind)

    rows = []
    fetched_at = date.today().isoformat()
    for i, d in enumerate(dates):
        rh_vals = hourly_by_date.get(d, {}).get("rh", [])
        wind_vals = hourly_by_date.get(d, {}).get("wind", [])
        rows.append((
            region["id"], d,
            daily["temperature_2m_min"][i],
            daily["temperature_2m_max"][i],
            daily["temperature_2m_mean"][i],
            mean(rh_vals) if rh_vals else None,
            daily["precipitation_sum"][i],
            (daily["sunshine_duration"][i] or 0) / 3600,  # detik -> jam
            daily["wind_speed_10m_max"][i],
            mean(wind_vals) if wind_vals else None,
            fetched_at,
        ))
    return rows


def main():
    regions = fetch_all("SELECT id, centroid_lat, centroid_lon FROM regions")
    if not regions:
        print("Tabel regions kosong -- jalankan seed_regions.py dulu.", file=sys.stderr)
        sys.exit(1)

    end = date.today()
    start = end - timedelta(days=5 * 365)

    total = 0
    for idx, region in enumerate(regions, 1):
        rows = fetch_region_weather(region, start.isoformat(), end.isoformat())
        n = bulk_insert(
            "raw_weather",
            ["region_id", "date", "temp_min", "temp_max", "temp_mean",
             "humidity_avg", "rainfall", "sunshine_hours", "wind_max", "wind_avg", "fetched_at"],
            rows,
        )
        total += n
        if idx % 25 == 0:
            print(f"[{idx}/{len(regions)}] region_id={region['id']} -> {n} baris")
        time.sleep(0.2)  # sopan ke Open-Meteo, gak ada rate limit resmi tapi tetap dijaga

    print(f"Selesai. Total baris raw_weather: {total}")


if __name__ == "__main__":
    main()
