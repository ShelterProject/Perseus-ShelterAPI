"""Tipis wrapper di atas koneksi PostgreSQL (Aiven).

Kredensial (PG_HOST, PG_PORT, PG_USER, PG_PASSWORD, PG_DATABASE) dan
sertifikat CA (PG_CA_CERT -- isi file .pem lengkap, bukan path) semuanya
dari environment variable -- jangan pernah di-hardcode (repo public). Aiven
mewajibkan TLS terverifikasi (sslmode=verify-ca), jadi CA cert wajib ada;
kita tulis ke file temp sekali per proses lalu dipakai psycopg2.
"""
import os
import tempfile

import psycopg2
import psycopg2.extras

_ca_file_path: str | None = None


def _ca_cert_path() -> str:
    global _ca_file_path
    if _ca_file_path:
        return _ca_file_path
    fd, path = tempfile.mkstemp(suffix=".pem")
    with os.fdopen(fd, "w") as f:
        f.write(os.environ["PG_CA_CERT"])
    _ca_file_path = path
    return path


def get_connection():
    return psycopg2.connect(
        host=os.environ["PG_HOST"],
        port=os.environ.get("PG_PORT", "5432"),
        user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"],
        dbname=os.environ["PG_DATABASE"],
        sslmode="verify-ca",
        sslrootcert=_ca_cert_path(),
    )


def execute(sql: str, params: tuple | None = None):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def fetch_all(sql: str, params: tuple | None = None) -> list[dict]:
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def bulk_insert(table: str, columns: list[str], rows: list[tuple],
                 on_conflict: str = "replace", conflict_target: str | None = None,
                 chunk_size: int = 2000) -> int:
    """Insert banyak baris pakai bound parameter asli (Postgres gak punya
    limit parameter seketat D1, jadi gak perlu inline-literal lagi).

    `conflict_target` = nama kolom unique/PK yang jadi acuan konflik, mis.
    "region_id, date" (WAJIB diisi kalau on_conflict != "abort").
    `on_conflict`:
      - "replace": ON CONFLICT (...) DO UPDATE SET <kolom lain> = EXCLUDED.<kolom>
      - "ignore":  ON CONFLICT (...) DO NOTHING
      - "abort":   insert biasa, gagal kalau ada conflict
    """
    if not rows:
        return 0

    col_list = ", ".join(columns)
    if on_conflict == "ignore":
        suffix = f" ON CONFLICT ({conflict_target}) DO NOTHING"
    elif on_conflict == "replace":
        if not conflict_target:
            raise ValueError("conflict_target wajib diisi kalau on_conflict='replace'")
        target_cols = {c.strip() for c in conflict_target.split(",")}
        update_cols = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c not in target_cols)
        suffix = f" ON CONFLICT ({conflict_target}) DO UPDATE SET {update_cols}"
    else:
        suffix = ""

    sql = f"INSERT INTO {table} ({col_list}) VALUES %s{suffix}"

    conn = get_connection()
    written = 0
    try:
        with conn.cursor() as cur:
            for i in range(0, len(rows), chunk_size):
                chunk = rows[i:i + chunk_size]
                psycopg2.extras.execute_values(cur, sql, chunk, page_size=len(chunk))
                written += len(chunk)
        conn.commit()
    finally:
        conn.close()
    return written
