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
.github/workflows/   Cron bulanan yang menjalankan pipeline/, + deploy API
pipeline/             Script Python: fetch data mentah + training
api/                  AWS Lambda API (Node.js) yang dibaca Mobile App
worker/               (deprecated, lihat "Kenapa AWS Lambda, bukan Cloudflare Workers")
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
AWS Lambda (Function URL, dicek header X-App-Key)
  -> query Postgres LANGSUNG tiap request (gak ada cache, data selalu fresh)
  -> expose endpoint ke Mobile App
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

## Kenapa AWS Lambda, bukan Cloudflare Workers

`worker/` (Cloudflare Workers + TypeScript) adalah percobaan pertama buat
API-nya, tapi dua pendekatan koneksi ke Postgres dari runtime Workers
sama-sama gak reliable:

- Socket TCP langsung (`nodejs_compat`) -- TLS handshake-nya retry-storm
  kena limit "too many subrequests" Workers, atau kadang malah hang total
  tanpa error sama sekali.
- Hyperdrive -- secara arsitektur dirancang buat kasus ini, tapi tetap
  diputuskan untuk tidak dipakai di proyek ini.

Lambda jalan di runtime Node biasa (bukan isolate V8 kayak Workers), jadi
koneksinya ke Aiven sama persis caranya kayak pipeline Python di GitHub
Actions yang dari awal sudah terbukti jalan. `worker/` dan
`deploy-worker.yml` ditinggalkan sebagai riwayat/dead code, bukan lagi
yang di-deploy.

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
`FIRMS_MAP_KEY`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `APP_KEY`.

- `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`: dari IAM user khusus deploy
  (lihat panduan deploy API di bawah) -- scope-nya cuma boleh Lambda + IAM
  role terbatas, bukan full admin.
- `APP_KEY`: satu string rahasia (generate sendiri, mis. `openssl rand -hex
  32`), ini "identifier" yang dicek API di header `X-App-Key` -- request
  yang gak bawa/gak cocok langsung ditolak sebelum nyentuh Postgres. Value
  yang sama ini juga yang ditanam di sisi Mobile App yang konsumsi API ini.

## Setup database & pipeline (sekali di awal)

1. Buat service PostgreSQL di [Aiven](https://aiven.io) (tier Free), copy
   `Host`, `Port`, `User`, `Password`, `Database name`, dan `CA certificate`
   dari tab Overview-nya.
2. Set secret `PG_HOST`, `PG_PORT`, `PG_USER`, `PG_PASSWORD`, `PG_DATABASE`,
   `PG_CA_CERT`, `FIRMS_MAP_KEY` di GitHub (Settings → Secrets and
   variables → Actions).
3. Ikuti panduan deploy API di bawah dulu (menerapkan `db/schema.sql` jadi
   bagian dari workflow deploy-nya).
4. Trigger manual **"Monthly prediction pipeline"** dari tab Actions --
   langkah pertamanya (`seed_regions.py`) otomatis isi 514 kabupaten/kota +
   zona seismik, lalu lanjut fetch & training.

Setelah itu, `monthly-pipeline.yml` jalan otomatis tiap tanggal 1, dan kalau
gagal di tengah jalan (rate limit dari sumber data eksternal, wajar pas
bootstrap awal narik 514 region sekaligus), `auto-retry-pipeline.yml`
otomatis trigger ulang -- gak perlu diklik manual. Pastikan **Settings →
Actions → General → Workflow permissions** di-set ke "Read and write
permissions" biar auto-retry ini bisa jalan.

## Deploy API ke AWS Lambda

API-nya (`api/index.mjs`) jalan sebagai AWS Lambda dengan **Function URL**
(HTTPS endpoint langsung dari Lambda, tanpa API Gateway -- API Gateway
cuma gratis 12 bulan, Lambda gratis permanen). Seluruh proses build & deploy
diotomatisasi oleh `.github/workflows/deploy-api.yml`; satu-satunya langkah
manual yang gak bisa diotomatisasi adalah menyiapkan kredensial AWS-nya,
karena itu butuh akses yang belum ada di titik mana pun sebelumnya.

### 1. Siapkan IAM user khusus deploy

Di AWS Console (IAM → Users → Create user), buat satu user khusus (misal
`perseus-shelter-ci`) dengan **custom policy** seminimal berikut (bukan
policy bawaan `AdministratorAccess`):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "lambda:*",
      "Resource": "arn:aws:lambda:*:*:function:perseus-shelter-api"
    },
    {
      "Effect": "Allow",
      "Action": ["iam:CreateRole", "iam:GetRole", "iam:AttachRolePolicy", "iam:PassRole"],
      "Resource": "arn:aws:iam::*:role/perseus-shelter-lambda-role"
    }
  ]
}
```

Generate access key buat user ini (Security credentials → Create access
key → "Application running outside AWS" / CLI).

### 2. Generate identifier akses API

```bash
openssl rand -hex 32
```

String ini dicek API di header `X-App-Key` pada setiap request -- siapa
pun yang mengonsumsi API ini (misal aplikasi mobile) harus mengirim header
ini dengan value yang sama. Request tanpa header ini ditolak sebelum API
sempat menyentuh database.

### 3. Set GitHub Secrets

Di repo ini: Settings → Secrets and variables → Actions, tambahkan:
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` (dari langkah 1), dan
`APP_KEY` (dari langkah 2). Secret `PG_*` dari bagian setup database di
atas dipakai ulang di sini, gak perlu dibuat dobel.

### 4. Jalankan workflow deploy

Trigger workflow **"Deploy API (AWS Lambda)"** dari tab Actions (manual,
`workflow_dispatch`) -- workflow ini akan otomatis, setiap kali dijalankan:

1. Menerapkan `db/schema.sql` ke Postgres (idempotent, aman diulang).
2. Membuat IAM role eksekusi Lambda kalau belum ada (`perseus-shelter-lambda-role`,
   cuma punya permission tulis CloudWatch Logs, gak ada akses AWS lain).
3. Build & deploy function-nya (`aws lambda create-function` / `update-function-code`).
4. Memasang **Reserved Concurrency = 10** -- batas keras berapa banyak
   eksekusi Lambda yang boleh jalan bersamaan. Ini dipasang di bawah limit
   koneksi bersamaan yang diberikan Aiven tier gratis (~20), supaya jumlah
   koneksi ke Postgres gak pernah melebihi slot yang tersedia. Efek
   sampingnya: ini juga jadi perlindungan dari traffic flood/DDoS, karena
   kelebihan request di luar 10 itu langsung di-throttle oleh Lambda
   sendiri (gratis, gak pernah ikut tereksekusi atau tertagih) sebelum
   sempat menyentuh kode atau database.
5. Membuat/mengaktifkan Function URL-nya dan menampilkan URL-nya di log
   step terakhir.

Push ke `main` yang mengubah isi folder `api/` atau `db/schema.sql` akan
otomatis memicu ulang workflow ini.

### Verifikasi

```bash
curl -H "X-App-Key: <APP_KEY yang di-generate di langkah 2>" \
  "<Function URL>/predictions/weather?lat=-6.2&lon=106.8"
```

Request tanpa header `X-App-Key` yang benar akan mendapat `401`. Request
ke path yang gak dikenal (lihat daftar endpoint di bagian "Alur data")
akan mendapat `404`.

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
+ training, dengan auto-retry di CI), dan `api/` (Lambda, konek Postgres
langsung + dibatasi satu `APP_KEY`) sudah siap di-deploy lewat
`deploy-api.yml`.

Belum dikerjakan: langkah "Notified" (push notification FCM saat ada
prediksi ekstrem), dan menghubungkan Mobile App ke endpoint API ini.
