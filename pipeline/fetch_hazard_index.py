"""Sample indeks bahaya banjir & longsor dari InaRISK BNPB (ImageServer)
per titik tengah kabupaten/kota, simpan ke hazard_index_flood /
hazard_index_landslide.

BEDA dari fetch_weather/fetch_earthquake/fetch_hotspots: ini BUKAN
time-series dan BUKAN bagian job ML bulanan (gak ada training-nya, cuma
konversi index InaRISK ke skala 0-1 kita). Jalankan terpisah, siklusnya
lebih jarang (mis. tahunan / kapan pun InaRISK merilis update peta),
sesuai keputusan sebelumnya bahwa data ini sifatnya statis.
"""
import sys
from datetime import date

from lib.pg import bulk_insert, fetch_all
from lib.http import get_with_retry

LAYERS = {
    "flood": "INDEKS_BAHAYA_BANJIR",
    "landslide": "INDEKS_BAHAYA_TANAHLONGSOR",
}
IDENTIFY_URL_TMPL = (
    "https://gis.bnpb.go.id/server/rest/services/inarisk/{layer}/ImageServer/identify"
)


def identify(layer_name: str, lat: float, lon: float) -> float | None:
    geometry = f'{{"x":{lon},"y":{lat},"spatialReference":{{"wkid":4326}}}}'
    params = {
        "geometry": geometry,
        "geometryType": "esriGeometryPoint",
        "returnGeometry": "false",
        "f": "json",
    }
    resp = get_with_retry(IDENTIFY_URL_TMPL.format(layer=layer_name), params=params, expect_json=True)
    value = resp.json().get("value")
    if value is None or value == "NoData":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def main():
    regions = fetch_all("SELECT id, centroid_lat, centroid_lon FROM regions")
    if not regions:
        print("Tabel regions kosong -- jalankan seed_regions.py dulu.", file=sys.stderr)
        sys.exit(1)

    fetched_at = date.today().isoformat()

    for hazard_type, layer_name in LAYERS.items():
        rows = []
        for region in regions:
            value = identify(layer_name, region["centroid_lat"], region["centroid_lon"])
            if value is None:
                continue  # gak ada data raster di titik itu (mis. daerah bukan zona rawan)
            rows.append((region["id"], value, layer_name, fetched_at))

        table = f"hazard_index_{hazard_type}"
        n = bulk_insert(table, ["region_id", "hazard_index", "source_layer", "fetched_at"], rows,
                         on_conflict="replace", conflict_target="region_id")
        print(f"{table}: {n} dari {len(regions)} region punya nilai")


if __name__ == "__main__":
    main()
