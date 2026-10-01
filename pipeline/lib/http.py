"""GET dengan retry + exponential backoff -- dipakai semua script fetch_*.

Sumber eksternal (Open-Meteo, USGS, FIRMS, InaRISK) sesekali timeout/putus
di tengah ratusan request berturutan; ini bukan kegagalan permanen, jadi
di-retry dulu sebelum bikin seluruh job gagal.
"""
import json
import sys
import time
import requests

DEFAULT_TIMEOUT = 60
MAX_RETRIES = 10
MAX_BACKOFF_SECONDS = 45  # batas atas tiap sleep -- biar gak ada jeda diam
                          # berbunyi "macet" (dulu bisa sampai 512s tanpa print apapun)


def _backoff(attempt: int) -> float:
    return min(2 ** attempt, MAX_BACKOFF_SECONDS)


def _sleep_with_log(seconds: float, reason: str):
    print(f"  retry dalam {seconds:.0f}s ({reason}) ...")
    sys.stdout.flush()
    time.sleep(seconds)


def get_with_retry(url: str, params: dict | None = None, timeout: int = DEFAULT_TIMEOUT,
                    max_retries: int = MAX_RETRIES, expect_json: bool = False) -> requests.Response:
    """`expect_json=True` buat endpoint yang seharusnya SELALU balikin JSON
    (Open-Meteo, USGS, InaRISK) -- kalau body-nya bukan JSON valid (server
    lagi throttle & balikin teks/HTML error dengan status 200, bukan 429),
    itu dianggap gagal & di-retry, bukan langsung diteruskan ke caller buat
    meledak di `resp.json()`. JANGAN dipakai buat endpoint non-JSON kayak
    FIRMS (CSV).

    Tiap percobaan (gagal atau nunggu) SELALU nge-print sesuatu -- jeda diam
    tanpa log sama sekali itu yang bikin run kelihatan macet padahal cuma
    lagi nunggu backoff.
    """
    last_exc = None
    for attempt in range(max_retries):
        print(f"[{attempt + 1}/{max_retries}] GET {url.split('?')[0]}")
        sys.stdout.flush()
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            if resp.status_code == 429:
                # Rate limit beneran (bukan error transient biasa) --
                # hormati Retry-After kalau server kasih tahu, kalau
                # enggak, backoff biasa (dibatasi MAX_BACKOFF_SECONDS).
                retry_after = resp.headers.get("Retry-After")
                wait = min(float(retry_after), MAX_BACKOFF_SECONDS * 2) if retry_after else _backoff(attempt)
                last_exc = requests.HTTPError(f"429 Too Many Requests: {url}", response=resp)
                if attempt < max_retries - 1:
                    _sleep_with_log(wait, "429 Too Many Requests")
                continue
            resp.raise_for_status()

            invalid_body = expect_json and _not_valid_json(resp.text)
            if not resp.text.strip() or invalid_body:
                # Kadang server balikin HTTP 200 tapi body kosong/bukan
                # JSON pas lagi throttle -- itu tetap kegagalan, walau
                # raise_for_status() gak nangkep ini (status-nya 200).
                reason = "kosong" if not resp.text.strip() else "bukan JSON valid"
                preview = resp.text[:300].replace("\n", " ")
                print(f"  Respons {reason} (HTTP 200), dianggap gagal & di-retry")
                print(f"  Isi body (300 char pertama): {preview!r}")
                print(f"  Header respons: {dict(resp.headers)}")
                last_exc = requests.RequestException(f"Invalid response body ({reason}): {url}", response=resp)
                if attempt < max_retries - 1:
                    _sleep_with_log(_backoff(attempt), reason)
                continue
            return resp
        except requests.RequestException as e:
            last_exc = e
            print(f"  Error: {e}")
            if attempt < max_retries - 1:
                _sleep_with_log(_backoff(attempt), type(e).__name__)
    raise last_exc


def _not_valid_json(text: str) -> bool:
    try:
        json.loads(text)
        return False
    except (json.JSONDecodeError, ValueError):
        return True
