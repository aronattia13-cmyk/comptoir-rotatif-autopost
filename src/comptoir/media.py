"""Download product photos and the logo, with a local cache."""
from __future__ import annotations

import hashlib
import io
from pathlib import Path

import requests
from PIL import Image

from . import net
from .catalog import STORE_DOMAIN

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "cache"
LOGO_URL = "https://lecomptoirdurotatif.com/cdn/shop/files/LogoComptoirRotatif.png?v=1754412782"
LOGO_PATH = ROOT / "assets" / "logo.png"


def candidate_urls(url: str) -> list[str]:
    """Shopify serves the same file from the store domain (/cdn/shop/files/...)
    and from cdn.shopify.com. Try the store domain first, then the original."""
    urls = []
    if "cdn.shopify.com" in url and "/files/" in url:
        urls.append(f"https://{STORE_DOMAIN}/cdn/shop/files/" + url.split("/files/", 1)[1])
    urls.append(url)
    return urls


def download_image(url: str, timeout: int = 30) -> Image.Image:
    CACHE.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(url.split("?")[0].encode()).hexdigest()[:16]
    f = CACHE / f"{key}.img"
    if not f.exists():
        last = None
        for u in candidate_urls(url):
            try:
                r = net.get(u, timeout=timeout)
                if not r.headers.get("content-type", "").startswith("image/"):
                    raise ValueError(f"pas une image: {u}")
                f.write_bytes(r.content)
                break
            except Exception as exc:  # try next candidate
                last = exc
        else:
            raise last
    img = Image.open(io.BytesIO(f.read_bytes()))
    img.load()
    return img


def ensure_logo() -> Path | None:
    """Use assets/logo.png if present, else fetch the site logo once."""
    if LOGO_PATH.exists():
        return LOGO_PATH
    try:
        download_image(LOGO_URL).save(LOGO_PATH)
        return LOGO_PATH
    except Exception:
        return None
