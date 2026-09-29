"""Isi tabel `provinces`, `regions`, `seismic_zones` di D1.

Sumber `data/regions.json` sudah ditarik sekali dari layer resmi InaRISK BNPB
(`batas_administrasi/MapServer/2`, Batas Kabupaten) -- 515 kabupaten/kota
se-Indonesia beserta titik tengah (centroid) tiap polygon-nya, dihitung
pakai shapely. Data ini statis/jarang berubah (batas administrasi gak
sering direvisi), jadi di-commit sebagai file, BUKAN ditarik ulang tiap
bulan seperti raw_weather/raw_earthquake/raw_hotspots.

Jalankan sekali di awal, atau ulang kalau `data/regions.json` di-update
manual (mis. ada pemekaran kabupaten baru).
"""
import json
from pathlib import Path

from lib.d1 import bulk_insert

DATA_DIR = Path(__file__).parent / "data"


def seed_provinces_and_regions():
    data = json.loads((DATA_DIR / "regions.json").read_text())

    province_rows = [(code, name) for code, name in data["provinces"].items()]
    n_prov = bulk_insert("provinces", ["code", "name"], province_rows)
    print(f"provinces: {n_prov} baris")

    region_rows = [
        (r["bps_code"], r["province_code"], r["name"], r["centroid_lat"], r["centroid_lon"])
        for r in data["regions"]
    ]
    n_reg = bulk_insert(
        "regions",
        ["bps_code", "province_code", "name", "centroid_lat", "centroid_lon"],
        region_rows,
    )
    print(f"regions: {n_reg} baris")


def seed_seismic_zones():
    zones = json.loads((DATA_DIR / "seismic_zones.json").read_text())
    rows = [(z["name"], z["min_lat"], z["max_lat"], z["min_lon"], z["max_lon"]) for z in zones]
    n = bulk_insert("seismic_zones", ["name", "min_lat", "max_lat", "min_lon", "max_lon"], rows)
    print(f"seismic_zones: {n} baris")


if __name__ == "__main__":
    seed_provinces_and_regions()
    seed_seismic_zones()
