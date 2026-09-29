"""Tipis wrapper di atas Cloudflare D1 HTTP API.

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


def bulk_insert(table: str, columns: list[str], rows: list[tuple], chunk_size: int = 200,
                 or_replace: bool = True):
    """Insert banyak baris sekaligus, di-chunk biar gak kena limit ukuran request.

    `rows` = list of tuple, urutan value harus sama persis dengan `columns`.
    """
    if not rows:
        return 0

    verb = "INSERT OR REPLACE INTO" if or_replace else "INSERT INTO"
    col_list = ", ".join(columns)
    written = 0

    for i in range(0, len(rows), chunk_size):
        chunk = rows[i:i + chunk_size]
        placeholders = ", ".join(
            "(" + ", ".join(["?"] * len(columns)) + ")" for _ in chunk
        )
        flat_params = [v for row in chunk for v in row]
        sql = f"{verb} {table} ({col_list}) VALUES {placeholders}"
        execute(sql, flat_params)
        written += len(chunk)

    return written


def fetch_all(table_query: str, params: list | None = None) -> list[dict]:
    result = execute(table_query, params)
    if not result:
        return []
    return result[0].get("results", [])
