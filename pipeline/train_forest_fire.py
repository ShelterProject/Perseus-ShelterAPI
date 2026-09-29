"""Decision Tree Regressor per kabupaten/kota -- algoritma & fitur sama
seperti notebook `Forest_Fire.ipynb` di Shelter_ML (StandardScaler +
DecisionTreeRegressor, target `confidence`), cuma datanya:
  - fitur cuaca: dari prediction_weather (hasil SARIMAX di atas, bukan lagi
    dari BMKG), harus dijalankan SETELAH train_weather.py
  - target/histori confidence: dari raw_hotspots (FIRMS)
  - jarak: haversine dari titik tengah kabupaten ke titik hotspot terdekat
    dalam histori (pengganti kolom `distance` di notebook lama)

Kalau histori hotspot suatu kabupaten terlalu sedikit buat dilatih (jarang
kebakaran di sana), region itu di-skip -- konsisten dengan notebook lama
yang juga men-drop kabupaten tanpa data cukup (Kota Binjai, Batubara).
"""
import sys
from datetime import date

import pandas as pd
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeRegressor

from lib.d1 import bulk_insert, fetch_all

MIN_TRAINING_ROWS = 20
FEATURE_COLS = ["temp_min", "temp_max", "temp_mean", "humidity_avg", "rainfall", "sunshine_hours", "wind_max", "wind_avg"]


def main():
    regions = fetch_all("SELECT id, centroid_lat, centroid_lon FROM regions")
    if not regions:
        print("Tabel regions kosong -- jalankan seed_regions.py dulu.", file=sys.stderr)
        sys.exit(1)

    generated_at = date.today().isoformat()
    total = 0
    skipped = 0

    for idx, region in enumerate(regions, 1):
        region_id = region["id"]

        hotspots = fetch_all(
            "SELECT acq_date, confidence FROM raw_hotspots WHERE region_id = ? AND confidence IS NOT NULL",
            [region_id],
        )
        if len(hotspots) < MIN_TRAINING_ROWS:
            skipped += 1
            continue

        hist_weather = fetch_all(
            f"SELECT date, {', '.join(FEATURE_COLS)} FROM raw_weather WHERE region_id = ?",
            [region_id],
        )
        if len(hist_weather) < MIN_TRAINING_ROWS:
            skipped += 1
            continue

        hotspot_df = pd.DataFrame(hotspots).groupby("acq_date")["confidence"].mean().reset_index()
        hotspot_df = hotspot_df.rename(columns={"acq_date": "date"})
        weather_df = pd.DataFrame(hist_weather)

        train_df = weather_df.merge(hotspot_df, on="date", how="inner").dropna()
        if len(train_df) < MIN_TRAINING_ROWS:
            skipped += 1
            continue

        X = train_df[FEATURE_COLS]
        y = train_df["confidence"]
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)

        model = DecisionTreeRegressor()
        model.fit(X_scaled, y)

        future_weather = fetch_all(
            f"SELECT date, {', '.join(FEATURE_COLS)} FROM prediction_weather WHERE region_id = ? ORDER BY date",
            [region_id],
        )
        if not future_weather:
            skipped += 1
            continue

        future_df = pd.DataFrame(future_weather).dropna(subset=FEATURE_COLS)
        if future_df.empty:
            skipped += 1
            continue

        X_future = scaler.transform(future_df[FEATURE_COLS])
        predictions = model.predict(X_future)

        rows = [
            (region_id, d, max(0.0, min(100.0, float(conf))), generated_at)
            for d, conf in zip(future_df["date"], predictions)
        ]
        n = bulk_insert(
            "prediction_forest_fire",
            ["region_id", "date", "confidence", "generated_at"],
            rows,
        )
        total += n
        if idx % 25 == 0:
            print(f"[{idx}/{len(regions)}] region_id={region_id} -> {n} baris")

    print(f"Selesai. Total baris prediction_forest_fire: {total} ({skipped} region di-skip karena data kurang)")


if __name__ == "__main__":
    main()
