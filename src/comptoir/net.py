"""HTTP GET with limited retries (429 / 5xx / network errors), honouring Retry-After."""
from __future__ import annotations

import time

import requests

USER_AGENT = "comptoir-social/1.0 (+https://lecomptoirdurotatif.com)"
RETRY_STATUS = {429, 500, 502, 503, 504}


def get(url: str, params=None, timeout: int = 30, retries: int = 4, backoff: float = 2.0,
        sleep=time.sleep) -> requests.Response:
    last = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, params=params, timeout=timeout, headers={"User-Agent": USER_AGENT})
            if r.status_code not in RETRY_STATUS:
                r.raise_for_status()
                return r
            last = requests.HTTPError(f"{r.status_code} for {url}", response=r)
            wait = r.headers.get("Retry-After")
            delay = float(wait) if wait and wait.replace(".", "", 1).isdigit() else backoff * 2 ** attempt
        except (requests.ConnectionError, requests.Timeout) as exc:
            last, delay = exc, backoff * 2 ** attempt
        if attempt < retries:
            sleep(min(delay, 60))
    raise last
