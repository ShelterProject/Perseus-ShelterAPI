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
db/schema.sql         Skema PostgreSQL (Aiven)
```

## Alur data

```
GitHub Actions (bulanan)
  -> fetch data rolling (Open-Meteo, USGS, NASA FIRMS, InaRISK BNPB) --
     incremental, cuma yang belum ada di DB, bukan 5 tahun ulang tiap bulan
  -> tulis raw data ke PostgreSQL (Aiven)
  -> jalankan SARIMAX / Decision Tree
  -> tulis hasil prediksi ke PostgreSQL
Cloudflare Workers API (TCP langsung ke Postgres, tanpa Hyperdrive)
  -> baca dari PostgreSQL, expose endpoint ke Mobile App
Mobile App
  -> resolve GPS user -> provinsi/kabupaten terdekat -> tampilkan prediksi
```

Detail lengkap ada di `db/schema.sql` (komentar per tabel).

## Kenapa PostgreSQL (Aiven), bukan Cloudflare D1

Awalnya pakai D1, tapi D1 free tier punya limit **keras jumlah baris
ditulis per hari** (bukan soal storage) -- kena pas bootstrap histori 5
tahun untuk 514 kabupaten/kota. Postgres gak punya limit semacam itu, cuma
dibatasi storage & koneksi. Aiven punya tier gratis permanen (1 CPU/1GB
RAM/1GB storage) tanpa kartu kredit kadaluarsa kayak free-trial provider
lain.

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
- Aiven mewajibkan koneksi TLS terverifikasi (`sslmode=verify-ca`) — CA
  certificate-nya (dari tab Overview di dashboard Aiven, field "CA
  certificate") disimpan utuh sebagai secret `PG_CA_CERT` (multi-baris,
  paste apa adanya termasuk baris `-----BEGIN CERTIFICATE-----`).

Daftar secret yang dibutuhkan:
`PG_HOST`, `PG_PORT`, `PG_USER`, `PG_PASSWORD`, `PG_DATABASE`, `PG_CA_CERT`,
`FIRMS_MAP_KEY`, `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN` (permission
**Workers Scripts: Edit**). Worker-nya konek Postgres TCP langsung (bukan
lewat Hyperdrive), kredensialnya pakai ulang `PG_*` yang sama lewat Worker
Secret (`wrangler secret put`, dilakukan otomatis di `deploy-worker.yml`) --
gak ada `HYPERDRIVE_ID` atau kredensial terpisah yang perlu dibuat.

## Setup (sekali di awal)

1. Buat service PostgreSQL di [Aiven](https://aiven.io) (tier Free), copy
   `Host`, `Port`, `User`, `Password`, `Database name`, dan `CA certificate`
   dari tab Overview-nya.
2. Set semua secret di atas di GitHub (Settings → Secrets and variables → Actions).
3. Trigger manual workflow **"Deploy Worker"** dari tab Actions -- ini
   menerapkan `db/schema.sql` ke Postgres, deploy Worker-nya, lalu set
   kredensial Postgres sebagai Worker Secret.
4. Trigger manual **"Monthly prediction pipeline"** dari tab Actions --
   langkah pertamanya (`seed_regions.py`) otomatis isi 514 kabupaten/kota +
   zona seismik, lalu lanjut fetch & training.

Setelah itu, `monthly-pipeline.yml` jalan otomatis tiap tanggal 1, dan kalau
gagal di tengah jalan (rate limit dari sumber data eksternal, wajar pas
bootstrap awal narik 514 region sekaligus), `auto-retry-pipeline.yml`
otomatis trigger ulang -- gak perlu diklik manual. Pastikan **Settings →
Actions → General → Workflow permissions** di-set ke "Read and write
permissions" biar auto-retry ini bisa jalan.

## Menjalankan pipeline manual (lokal)

```bash
cd pipeline
pip install -r requirements.txt
cp ../.env.example ../.env   # isi nilainya, lalu export atau pakai python-dotenv
python seed_regions.py        # sekali di awal
python fetch_weather.py
python fetch_earthquake.py
python fetch_hotspots.py
python train_weather.py       # harus setelah fetch_weather
python train_earthquake.py    # harus setelah fetch_earthquake
python train_forest_fire.py   # harus setelah train_weather & fetch_hotspots
```

## Auto-retry saat bootstrap

`auto-retry-pipeline.yml` mendengarkan hasil **"Monthly prediction
pipeline"** -- kalau gagal (mis. kena rate limit Open-Meteo/FIRMS di
tengah bootstrap 514 region), otomatis trigger ulang setelah jeda 2
menit. Aman karena `fetch_*.py` incremental (skip yang sudah ada), jadi
gak perlu diklik manual tiap kali gagal selama proses bootstrap awal.
Setelah semua region punya data lengkap, kegagalan jadi jarang (cuma
narik data ~30 hari baru/bulan), jadi auto-retry ini efeknya makin gak
kepake sendirinya.

## Status

Skema Postgres, seluruh sumber data, script `pipeline/` (fetch incremental
+ training, dengan auto-retry di CI), dan `worker/` (API, konek Postgres
TCP langsung tanpa Hyperdrive) sudah siap di-deploy lewat `deploy-worker.yml`.

Belum dikerjakan: langkah "Notified" (push notification FCM saat ada
prediksi ekstrem), dan menghubungkan Mobile App ke endpoint API ini.
