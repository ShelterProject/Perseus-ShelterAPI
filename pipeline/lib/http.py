"""GET dengan retry + exponential backoff -- dipakai semua script fetch_*.

Sumber eksternal (Open-Meteo, USGS, FIRMS, InaRISK) sesekali timeout/putus
di tengah ratusan request berturutan; ini bukan kegagalan permanen, jadi
di-retry dulu sebelum bikin seluruh job gagal.
"""
import json
import time
import requests

DEFAULT_TIMEOUT = 60
MAX_RETRIES = 10


def get_with_retry(url: str, params: dict | None = None, timeout: int = DEFAULT_TIMEOUT,
                    max_retries: int = MAX_RETRIES, expect_json: bool = False) -> requests.Response:
    """`expect_json=True` buat endpoint yang seharusnya SELALU balikin JSON
    (Open-Meteo, USGS, InaRISK) -- kalau body-nya bukan JSON valid (server
    lagi throttle & balikin teks/HTML error dengan status 200, bukan 429),
    itu dianggap gagal & di-retry, bukan langsung diteruskan ke caller buat
    meledak di `resp.json()`. JANGAN dipakai buat endpoint non-JSON kayak
    FIRMS (CSV)."""
    last_exc = None
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            if resp.status_code == 429:
                # Rate limit beneran (bukan error transient biasa) --
                # hormati Retry-After kalau server kasih tahu, kalau
                # enggak, tunggu lebih lama dari backoff normal (tapi
                # dibatasi biar 10x retry gak jadi berjam-jam sendirian --
                # jeda antar-batch di fetch_weather.py yang jadi
                # pencegahan utama, ini cuma jaring pengaman).
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else min(20 * (attempt + 1), 120)
                print(f"429 Too Many Requests dari {url.split('?')[0]}, tunggu {wait:.0f}s ...")
                time.sleep(wait)
                last_exc = requests.HTTPError(f"429 Too Many Requests: {url}", response=resp)
                continue
            resp.raise_for_status()

            invalid_body = expect_json and _not_valid_json(resp.text)
            if not resp.text.strip() or invalid_body:
                # Kadang server balikin HTTP 200 tapi body kosong/bukan
                # JSON pas lagi throttle -- itu tetap kegagalan, walau
                # raise_for_status() gak nangkep ini (status-nya 200).
                reason = "kosong" if not resp.text.strip() else "bukan JSON valid"
                preview = resp.text[:300].replace("\n", " ")
                print(f"Respons {reason} (HTTP 200) dari {url.split('?')[0]}, dianggap gagal & di-retry")
                print(f"  Isi body (300 char pertama): {preview!r}")
                print(f"  Header respons: {dict(resp.headers)}")
                last_exc = requests.RequestException(f"Invalid response body ({reason}): {url}", response=resp)
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                continue
            return resp
        except requests.RequestException as e:
            last_exc = e
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # 1s, 2s, 4s, ...
    raise last_exc


def _not_valid_json(text: str) -> bool:
    try:
        json.loads(text)
        return False
    except (json.JSONDecodeError, ValueError):
        return True
