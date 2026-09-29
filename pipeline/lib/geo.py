"""Haversine -- teknik yang sama dipakai Shelter_ML buat cari titik/kota
terdekat (gempa -> kota terdekat, karhutla -> jarak ke kota). Dipakai ulang
di sini biar konsisten, bukan teknik baru.
"""
from math import radians, cos, sin, asin, sqrt


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 6371 * 2 * asin(sqrt(a))


def nearest(lat: float, lon: float, candidates: list[dict],
            lat_key: str = "centroid_lat", lon_key: str = "centroid_lon"):
    """candidates: list of dict yang punya lat_key/lon_key. Return (item, jarak_km)."""
    best, best_dist = None, float("inf")
    for c in candidates:
        d = haversine_km(lat, lon, c[lat_key], c[lon_key])
        if d < best_dist:
            best, best_dist = c, d
    return best, best_dist
