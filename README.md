# Perseus-ShelterAPI

Backend baru untuk ekosistem Shelter (companion dari
[`Perseus-ShelterMobile`](https://github.com/ShelterProject/Perseus-ShelterMobile)),
menggantikan `Shelter_Cloud` (PHP lama). Metode & algoritma prediksi tetap
sama seperti [`Shelter_ML`](https://github.com/zailbreck/Shelter_ML)
(SARIMAX untuk cuaca & gempa, Decision Tree Regressor per kabupaten untuk
karhutla) — yang berubah cuma sumber data (otomatis, bukan manual) dan
cakupan (seluruh Indonesia, bukan hanya Sumatera Utara).

## Struktur

```
.github/workflows/   Cron bulanan yang menjalankan pipeline/
pipeline/             Script Python: fetch data mentah + training
worker/               Cloudflare Workers API (TypeScript) yang dibaca Mobile App
d1/schema.sql         Skema Cloudflare D1
```

## Alur data

```
GitHub Actions (bulanan)
  -> fetch data 5 tahun rolling (Open-Meteo, USGS, NASA FIRMS, InaRISK BNPB)
  -> tulis raw data ke D1
  -> jalankan SARIMAX / Decision Tree
  -> tulis hasil prediksi ke D1
Cloudflare Workers API
  -> baca dari D1, expose endpoint ke Mobile App
Mobile App
  -> resolve GPS user -> provinsi/kabupaten terdekat -> tampilkan prediksi
```

Detail lengkap ada di `d1/schema.sql` (komentar per tabel).

## Sumber data (bukan lagi BMKG Data Online / SiPongi manual)

| Data | Sumber | Catatan |
|---|---|---|
| Cuaca | Open-Meteo (`api.open-meteo.com`, `archive-api.open-meteo.com`) | gratis, tanpa API key |
| Gempa | USGS Earthquake Catalog (`earthquake.usgs.gov`) | gratis, tanpa API key, dipecah per zona seismik |
| Karhutla | NASA FIRMS (`firms.modaps.eosdis.nasa.gov`) | butuh `FIRMS_MAP_KEY` gratis (self-service), limit 5000 transaksi/10 menit |
| Banjir & Longsor | InaRISK BNPB (`gis.bnpb.go.id`, ArcGIS ImageServer) | indeks bahaya 0-1, statis/di-refresh terpisah dari siklus bulanan |

## Secrets

Repo ini **public** — tidak ada credential yang boleh di-hardcode.

- Lokal: copy `.env.example` ke `.env` (sudah di-gitignore).
- CI (GitHub Actions): simpan sebagai **repository secret** (Settings →
  Secrets and variables → Actions), bukan sebagai env di file workflow.
- Cloudflare: `CLOUDFLARE_API_TOKEN` juga disimpan sebagai GitHub secret,
  dipakai job Actions buat nulis ke D1 lewat Wrangler/REST API.

## Status

Skema D1 dan sumber data sudah diverifikasi. Script `pipeline/` dan
`worker/` menyusul.
