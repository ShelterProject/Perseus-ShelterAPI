"""Tipis wrapper di atas Cloudflare D1 HTTP API.

DIPERTAHANKAN CUMA BUAT `migrate_d1_to_pg.py` -- pipeline fetch_*/train_*
sudah pindah ke Postgres (`lib/pg.py`) karena D1 free tier kena limit
harian jumlah baris ditulis. Jangan dipakai lagi di script baru.

Kredensial (CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_API_TOKEN, D1_DATABASE_ID)
selalu dari environment variable -- jangan pernah di-hardcode, karena repo
ini public. Di GitHub Actions, isi lewat repository secrets.
"""
import os
import time
import requests

_BASE = "https://api.cloudflare.com/client/v4"


def _config():
    account_id = os.environ["CLOUDFLARE_ACCOUNT_ID"]
    api_token = os.environ["CLOUDFLARE_API_TOKEN"]
    database_id = os.environ["D1_DATABASE_ID"]
    return account_id, api_token, database_id


def execute(sql: str, params: list | None = None, retries: int = 3):
    """Jalankan satu statement SQL (boleh multi-row VALUES (...),(...))."""
    account_id, api_token, database_id = _config()
    url = f"{_BASE}/accounts/{account_id}/d1/database/{database_id}/query"
    headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}
    payload = {"sql": sql, "params": params or []}

    last_err = None
    for attempt in range(retries):
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            if not data.get("success"):
                raise RuntimeError(f"D1 query failed: {data.get('errors')}")
            return data["result"]
        last_err = resp.text
        time.sleep(2 ** attempt)
    raise RuntimeError(f"D1 query failed after {retries} attempts: {last_err}")


def _sql_literal(value) -> str:
    """Inline value langsung ke teks SQL (bukan bound parameter).

    D1 membatasi jumlah bound parameter per statement jauh lebih ketat
    daripada SQLite biasa (gagal di sekitar ratusan, bukan 999) -- kalau
    dipakai buat bulk insert ribuan baris, batasnya cepat kena. Data yang
    di-insert di sini semuanya berasal dari pipeline kita sendiri (bukan
    input user), jadi inlining dengan escaping manual ini aman dari SQL
    injection, dan cuma dibatasi ukuran payload request, bukan jumlah
    parameter.
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


def bulk_insert(table: str, columns: list[str], rows: list[tuple], chunk_size: int = 500,
                 on_conflict: str = "replace"):
    """Insert banyak baris sekaligus, di-chunk biar payload request gak raksasa.

    `rows` = list of tuple, urutan value harus sama persis dengan `columns`.
    `on_conflict`:
      - "replace" (default): INSERT OR REPLACE -- cocok buat tabel yang gak
        di-referensikan FOREIGN KEY dari tabel lain (mis. prediction_*,
        raw_earthquake_events/raw_hotspots yang di-dedup lewat unique index).
      - "ignore": INSERT OR IGNORE -- WAJIB dipakai buat tabel referensi yang
        primary key-nya di-FOREIGN KEY-kan dari tabel lain (regions,
        provinces, seismic_zones). REPLACE bakal delete+insert ulang baris
        (ganti `id` autoincrement-nya), yang mematahkan FK dari raw_weather/
        prediction_* yang sudah terlanjur mereferensikan id lama.
      - "abort" (default SQLite): INSERT INTO biasa, gagal kalau ada conflict.
    """
    if not rows:
        return 0

    verb = {
        "replace": "INSERT OR REPLACE INTO",
        "ignore": "INSERT OR IGNORE INTO",
        "abort": "INSERT INTO",
    }[on_conflict]
    col_list = ", ".join(columns)
    written = 0

    for i in range(0, len(rows), chunk_size):
        chunk = rows[i:i + chunk_size]
        values_sql = ", ".join(
            "(" + ", ".join(_sql_literal(v) for v in row) + ")" for row in chunk
        )
        sql = f"{verb} {table} ({col_list}) VALUES {values_sql}"
        execute(sql)
        written += len(chunk)

    return written


def fetch_all(table_query: str, params: list | None = None) -> list[dict]:
    result = execute(table_query, params)
    if not result:
        return []
    return result[0].get("results", [])
