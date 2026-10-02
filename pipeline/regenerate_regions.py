"""Cara `data/regions.json` dibuat -- dijalankan sekali secara manual, BUKAN
bagian pipeline otomatis. Simpan di sini biar reproducible kalau suatu saat
ada pemekaran kabupaten baru dan datanya perlu di-refresh.

Sumber: layer resmi InaRISK BNPB (`batas_administrasi/MapServer/2`,
Batas Kabupaten), field KDPPUM/KDBBPS = kode BPS. Centroid dihitung dari
polygon tiap kabupaten pakai shapely (geometri di-generalize dikit lewat
`maxAllowableOffset` biar payload-nya gak berat, cukup akurat buat titik
representatif -- bukan buat analisis spasial presisi tinggi).
"""
import json
from pathlib import Path

import requests
from shapely.geometry import shape

QUERY_URL = (
    "https://gis.bnpb.go.id/server/rest/services/inarisk/batas_administrasi/MapServer/2/query"
)
OUT_PATH = Path(__file__).parent / "data" / "regions.json"


def main():
    params = {
        "where": "1=1",
        "outFields": "KDPPUM,KDBBPS,WADMKK,WADMPR",
        "returnGeometry": "true",
        "maxAllowableOffset": "0.05",
        "outSR": "4326",
        "f": "geojson",
    }
    resp = requests.get(QUERY_URL, params=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()

    provinces = {}
    by_bps_code = {}
    for feature in data["features"]:
        props = feature["properties"]
        geom = feature.get("geometry")
        kdppum, kdbbps = props.get("KDPPUM"), props.get("KDBBPS")
        name, prov = props.get("WADMKK"), props.get("WADMPR")
        # `name.strip()` -- InaRISK kadang punya fitur polygon duplikat
        # per kabupaten (mis. pulau kecil/exclave terpisah) dengan field
        # WADMKK isinya cuma spasi kosong. `if name` doang gak nangkep itu
        # ("  " tetap truthy di Python), jadi harus di-strip dulu.
        if not (geom and kdppum and kdbbps and name and name.strip()):
            continue
        centroid = shape(geom).centroid
        if kdbbps in by_bps_code:
            print(f"PERINGATAN: bps_code {kdbbps} duplikat ({by_bps_code[kdbbps]['name']!r} vs "
                  f"{name!r}) -- pakai yang pertama ketemu, cek manual kalau curiga salah.")
            continue
        by_bps_code[kdbbps] = {
            "bps_code": kdbbps,
            "province_code": kdppum,
            "name": name,
            "province_name": prov,
            "centroid_lat": round(centroid.y, 5),
            "centroid_lon": round(centroid.x, 5),
        }
        provinces[kdppum] = prov

    regions = list(by_bps_code.values())
    OUT_PATH.write_text(
        json.dumps({"provinces": provinces, "regions": regions}, ensure_ascii=False, indent=2)
    )
    print(f"{len(regions)} kabupaten/kota, {len(provinces)} provinsi -> {OUT_PATH}")


if __name__ == "__main__":
    main()
