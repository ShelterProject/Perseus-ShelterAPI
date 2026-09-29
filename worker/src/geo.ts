// Haversine -- sama seperti versi Python di pipeline/lib/geo.py, dipakai di
// sini buat resolve GPS user ke kabupaten/kota terdekat. D1 (SQLite) gak
// punya fungsi trigonometri bawaan yang bisa diandalkan lintas versi, jadi
// perhitungan jarak dilakukan di JS terhadap tabel `regions` (cuma ~515
// baris, murah buat di-scan tiap request).

export interface Region {
  id: number;
  province_code: string;
  bps_code: string;
  name: string;
  centroid_lat: number;
  centroid_lon: number;
}

export function haversineKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const toRad = (deg: number) => (deg * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 6371 * 2 * Math.asin(Math.sqrt(a));
}

export function nearestRegion(lat: number, lon: number, regions: Region[]): { region: Region; distanceKm: number } | null {
  let best: Region | null = null;
  let bestDist = Infinity;
  for (const r of regions) {
    const d = haversineKm(lat, lon, r.centroid_lat, r.centroid_lon);
    if (d < bestDist) {
      best = r;
      bestDist = d;
    }
  }
  return best ? { region: best, distanceKm: bestDist } : null;
}

export function regionsWithinRadius(lat: number, lon: number, regions: Region[], radiusKm: number): Region[] {
  return regions.filter((r) => haversineKm(lat, lon, r.centroid_lat, r.centroid_lon) <= radiusKm);
}
