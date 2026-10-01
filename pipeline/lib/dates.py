"""Helper tanggal bersama buat fetch_*.py."""
from datetime import date


def bootstrap_start_date(end: date, years: int = 5) -> date:
    """Awal window bootstrap -- SELALU 1 Januari, bukan geser ngikutin
    tanggal persis job-nya kebetulan jalan.

    Contoh: job jalan Desember 2026 -> mulai dari 1 Januari 2021 (genap
    5 tahun kalender), BUKAN `end - 5*365 hari` (yang hasilnya sekitar
    Desember 2021, cuma 4 tahun + sebulan data kalau kebetulan run-nya
    akhir tahun).
    """
    return date(end.year - years, 1, 1)
