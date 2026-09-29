"""SARIMAX per variabel cuaca, per kabupaten/kota -- order & seasonal_order
PERSIS sama dengan notebook `Cuaca_Earthquake.ipynb` di Shelter_ML, cuma
sumber datanya (raw_weather, dari Open-Meteo) dan cakupannya (per region,
seluruh Indonesia) yang beda.

Forecast 365 hari ke depan dari hari job ini jalan (window rolling, bukan
fixed 2021 seperti dulu).
"""
import sys
import warnings
from datetime import date, timedelta

import pandas as pd
from statsmodels.tsa.statespace.sarimax import SARIMAX

from lib.pg import bulk_insert, fetch_all

warnings.filterwarnings("ignore")

VARIABLES = ["temp_min", "temp_max", "temp_mean", "humidity_avg", "rainfall", "sunshine_hours", "wind_max", "wind_avg"]
FORECAST_DAYS = 365
ORDER = (1, 1, 1)
SEASONAL_ORDER = (1, 1, 1, 12)


def condition_from(rainfall: float) -> str:
    # Logika sederhana pengganti field `cuaca` kategorikal di dataset lama,
    # diturunkan dari curah hujan harian (mm) -- bukan hasil model terpisah.
    if rainfall >= 50:
        return "Hujan Deras"
    if rainfall >= 20:
        return "Hujan"
    if rainfall >= 1:
        return "Mendung"
    return "Cerah"


def forecast_variable(series: pd.Series) -> pd.Series:
    series = series.dropna()
    if len(series) < 60:
        return pd.Series(dtype=float)
    model = SARIMAX(series, order=ORDER, seasonal_order=SEASONAL_ORDER,
                     enforce_stationarity=False, enforce_invertibility=False)
    results = model.fit(disp=False)
    forecast = results.get_forecast(steps=FORECAST_DAYS)
    return forecast.predicted_mean


def main():
    regions = fetch_all("SELECT id FROM regions")
    if not regions:
        print("Tabel regions kosong -- jalankan seed_regions.py dulu.", file=sys.stderr)
        sys.exit(1)

    generated_at = date.today().isoformat()
    total = 0

    for idx, region in enumerate(regions, 1):
        region_id = region["id"]
        raw = fetch_all(
            "SELECT date, temp_min, temp_max, temp_mean, humidity_avg, rainfall, "
            "sunshine_hours, wind_max, wind_avg FROM raw_weather WHERE region_id = %s ORDER BY date",
            [region_id],
        )
        if len(raw) < 60:
            print(f"[{idx}/{len(regions)}] region_id={region_id}: data kurang, skip")
            continue

        df = pd.DataFrame(raw)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")

        forecasts = {}
        for var in VARIABLES:
            forecasts[var] = forecast_variable(df[var])

        if forecasts["rainfall"].empty:
            print(f"[{idx}/{len(regions)}] region_id={region_id}: forecast gagal, skip")
            continue

        future_dates = forecasts["rainfall"].index
        rows = []
        for d in future_dates:
            date_str = d.date().isoformat()
            values = {var: forecasts[var].get(d) for var in VARIABLES}
            rows.append((
                region_id, date_str,
                values["temp_min"], values["temp_max"], values["temp_mean"],
                values["humidity_avg"], values["rainfall"], values["sunshine_hours"],
                values["wind_max"], values["wind_avg"],
                condition_from(values["rainfall"] or 0),
                generated_at,
            ))

        n = bulk_insert(
            "prediction_weather",
            ["region_id", "date", "temp_min", "temp_max", "temp_mean", "humidity_avg",
             "rainfall", "sunshine_hours", "wind_max", "wind_avg", "condition", "generated_at"],
            rows,
            on_conflict="replace", conflict_target="region_id, date",
        )
        total += n
        if idx % 25 == 0:
            print(f"[{idx}/{len(regions)}] region_id={region_id} -> {n} baris forecast")

    print(f"Selesai. Total baris prediction_weather: {total}")


if __name__ == "__main__":
    main()
