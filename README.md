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

## Setup (sekali di awal)

1. Buat database: `wrangler d1 create perseus-shelter-db`, isi
   `database_id` hasilnya ke `worker/wrangler.toml` (jangan commit kalau
   repo tetap public — override lokal atau lewat Cloudflare dashboard).
2. Terapkan skema: `wrangler d1 execute perseus-shelter-db --remote --file=d1/schema.sql`
3. Set GitHub Secrets (Settings → Secrets and variables → Actions):
   `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`, `D1_DATABASE_ID`, `FIRMS_MAP_KEY`
4. Isi data referensi wilayah (sekali, sebelum job bulanan pertama):
   `cd pipeline && pip install -r requirements.txt && python seed_regions.py`
5. Deploy Worker: `cd worker && npm install && npm run deploy`

Setelah itu, `monthly-pipeline.yml` jalan otomatis tiap tanggal 1. Bisa juga
di-trigger manual lewat tab Actions → "Run workflow" buat testing.

## Menjalankan pipeline manual (lokal)

```bash
cd pipeline
pip install -r requirements.txt
cp ../.env.example ../.env   # isi nilainya, lalu export atau pakai python-dotenv
python fetch_weather.py
python fetch_earthquake.py
python fetch_hotspots.py
python train_weather.py       # harus setelah fetch_weather
python train_earthquake.py    # harus setelah fetch_earthquake
python train_forest_fire.py   # harus setelah train_weather & fetch_hotspots
```

## Status

Skema D1, seluruh sumber data, script `pipeline/` (fetch + training), dan
`worker/` (API) sudah ada. Belum dikerjakan: langkah "Notified" (push
notification FCM saat ada prediksi ekstrem) dan deploy end-to-end pertama.
