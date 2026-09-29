"""SARIMAX per variabel gempa (lintang, bujur, magnitudo, kedalaman) per
zona seismik -- order/seasonal_order sama seperti notebook lama, cuma
sekarang per-zona (bukan 1 model nasional) dan raw event dari USGS
diagregasi jadi rata-rata harian dulu (avg lintang/bujur/magnitudo/
kedalaman) persis logika `df_hasil_*` di notebook.

Setelah forecast keluar, tiap hari hasilnya di-resolve ke kabupaten
terdekat pakai haversine -- pengganti fungsi `terdekat()` yang dulu pakai
dataset GeoNames.
"""
import sys
import warnings
from datetime import date

import pandas as pd
from statsmodels.tsa.statespace.sarimax import SARIMAX

from lib.pg import bulk_insert, fetch_all
from lib.geo import nearest

warnings.filterwarnings("ignore")

FORECAST_DAYS = 365
ORDER = (1, 1, 1)
SEASONAL_ORDER = (1, 1, 0, 12)


def forecast_variable(series: pd.Series):
    series = series.dropna()
    if len(series) < 60:
        return None
    model = SARIMAX(series, order=ORDER, seasonal_order=SEASONAL_ORDER,
                     enforce_stationarity=False, enforce_invertibility=False)
    results = model.fit(disp=False)
    forecast = results.get_forecast(steps=FORECAST_DAYS)
    return forecast.predicted_mean, forecast.conf_int()


def main():
    zones = fetch_all("SELECT id, name FROM seismic_zones")
    regions = fetch_all("SELECT id, centroid_lat, centroid_lon FROM regions")
    if not zones or not regions:
        print("Tabel seismic_zones/regions kosong -- jalankan seed_regions.py dulu.", file=sys.stderr)
        sys.exit(1)

    generated_at = date.today().isoformat()
    total = 0

    for zone in zones:
        events = fetch_all(
            "SELECT event_time, lat, lon, magnitude, depth_km FROM raw_earthquake_events "
            "WHERE seismic_zone_id = %s ORDER BY event_time",
            [zone["id"]],
        )
        if len(events) < 60:
            print(f"Zona '{zone['name']}': event kurang ({len(events)}), skip")
            continue

        df = pd.DataFrame(events)
        df["event_time"] = pd.to_datetime(df["event_time"])
        daily = df.groupby("event_time").agg(
            lat_avg=("lat", "mean"), lat_min=("lat", "min"), lat_max=("lat", "max"),
            lon_avg=("lon", "mean"), lon_min=("lon", "min"), lon_max=("lon", "max"),
            magnitude_avg=("magnitude", "mean"),
            depth_min_km=("depth_km", "min"), depth_max_km=("depth_km", "max"),
        )
        # isi hari kosong (gak ada gempa) biar time-series-nya kontinu, SARIMAX butuh itu
        full_index = pd.date_range(daily.index.min(), daily.index.max(), freq="D")
        daily = daily.reindex(full_index).interpolate()

        results = {}
        for col in ["lat_avg", "lon_avg", "magnitude_avg", "depth_min_km", "depth_max_km"]:
            out = forecast_variable(daily[col])
            if out is None:
                break
            results[col] = out[0]
        if len(results) < 5:
            print(f"Zona '{zone['name']}': forecast gagal, skip")
            continue

        future_dates = results["lat_avg"].index
        rows = []
        for d in future_dates:
            lat_avg = results["lat_avg"].get(d)
            lon_avg = results["lon_avg"].get(d)
            nearest_region, _ = nearest(lat_avg, lon_avg, regions) if lat_avg and lon_avg else (None, None)
            rows.append((
                zone["id"], d.date().isoformat(),
                lat_avg, None, None,  # lat_min/lat_max: dataset resample gak eksplisit hitung, cukup avg utk skala nasional
                lon_avg, None, None,
                results["magnitude_avg"].get(d),
                results["depth_min_km"].get(d), results["depth_max_km"].get(d),
                nearest_region["id"] if nearest_region else None,
                generated_at,
            ))

        n = bulk_insert(
            "prediction_earthquake",
            ["seismic_zone_id", "date", "lat_avg", "lat_min", "lat_max",
             "lon_avg", "lon_min", "lon_max", "magnitude_avg",
             "depth_min_km", "depth_max_km", "nearest_region_id", "generated_at"],
            rows,
            on_conflict="replace", conflict_target="seismic_zone_id, date",
        )
        total += n
        print(f"Zona '{zone['name']}': {n} baris forecast")

    print(f"Selesai. Total baris prediction_earthquake: {total}")


if __name__ == "__main__":
    main()
