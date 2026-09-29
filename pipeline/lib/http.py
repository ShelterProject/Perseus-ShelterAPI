"""GET dengan retry + exponential backoff -- dipakai semua script fetch_*.

Sumber eksternal (Open-Meteo, USGS, FIRMS, InaRISK) sesekali timeout/putus
di tengah ratusan request berturutan; ini bukan kegagalan permanen, jadi
di-retry dulu sebelum bikin seluruh job gagal.
"""
import time
import requests

DEFAULT_TIMEOUT = 60
MAX_RETRIES = 4


def get_with_retry(url: str, params: dict | None = None, timeout: int = DEFAULT_TIMEOUT,
                    max_retries: int = MAX_RETRIES) -> requests.Response:
    last_exc = None
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last_exc = e
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # 1s, 2s, 4s, ...
    raise last_exc
