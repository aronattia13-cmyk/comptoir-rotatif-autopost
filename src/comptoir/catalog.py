"""Shopify catalogue retrieval and normalisation.

Primary source: the store's public ``/products.json`` endpoint (no token, no app,
no subscription). Optional: Shopify Storefront API when SHOPIFY_STOREFRONT_TOKEN
is set. Both feed the same ``Product`` model.

Rules enforced here:
- prices are copied verbatim from Shopify (string -> Decimal), never computed;
- unavailable products are flagged and excluded by ``publishable()``;
- lot size (5 / 10 / 50 ...) is read from the product title, never guessed from
  the handle (handles are often stale: "10-turbines..." is a lot of 50).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, asdict
from decimal import Decimal, InvalidOperation
from typing import Iterable

import requests

STORE_DOMAIN = "lecomptoirdurotatif.com"
USER_AGENT = "comptoir-social/1.0 (+https://lecomptoirdurotatif.com)"

LOT_RE = re.compile(r"^\s*lot\s+de\s+(\d+)\s+", re.IGNORECASE)

# Words that must keep a fixed spelling when titles are normalised.
FIXED_WORDS = {
    "LED": "LED", "EMS": "EMS", "DTE": "DTE", "NSK": "NSK",
    "KAVO": "KaVo", "WOODPECKER": "Woodpecker", "SATELEC": "Satelec",
    "MULTIFLEX": "Multiflex", "W&H": "W&H",
}
# Plain typo corrections observed in the catalogue (display only).
TYPOS = {
    "COMPTATIBLES": "compatibles", "COMPTATIBLE": "compatible",
    "DETARTREURS": "détartreurs", "DETARTREUR": "détartreur",
}

CATEGORIES = ("turbine", "contre-angle", "detartreur", "autre")


class CatalogError(RuntimeError):
    pass


@dataclass
class Product:
    id: str
    handle: str
    title: str                 # raw Shopify title
    display_name: str          # cleaned name shown on visuals (no lot prefix)
    reference: str             # SKU, or handle when the SKU is empty
    reference_is_sku: bool
    price: Decimal             # verbatim Shopify price (EUR)
    compare_at_price: Decimal | None
    lot_size: int              # 1 when the title has no "Lot de N"
    category: str
    available: bool
    image_url: str | None
    url: str
    image_urls: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["price"] = str(self.price)
        d["compare_at_price"] = str(self.compare_at_price) if self.compare_at_price else None
        return d


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #
def parse_price(raw) -> Decimal:
    if raw is None or raw == "":
        raise CatalogError("prix manquant")
    try:
        value = Decimal(str(raw))
    except InvalidOperation as exc:
        raise CatalogError(f"prix illisible: {raw!r}") from exc
    if value <= 0:
        raise CatalogError(f"prix nul ou négatif: {raw!r}")
    return value


def parse_lot_size(title: str) -> int:
    m = LOT_RE.match(title)
    return int(m.group(1)) if m else 1


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def detect_category(title: str, product_type: str = "") -> str:
    t = _strip_accents(f"{product_type} {title}").lower()
    if "turbine" in t and "raccord rapide type" not in t:
        return "turbine"
    if "contre angle" in t or "contre-angle" in t:
        return "contre-angle"
    if "detartreur" in t:
        return "detartreur"
    return "autre"


def clean_name(title: str) -> str:
    """'Lot de 50 DETARTREURS COMPTATIBLES EMS WOODPECKER AVEC LED'
    -> 'Détartreurs compatibles EMS Woodpecker avec LED'"""
    name = LOT_RE.sub("", title).strip()
    name = re.sub(r"\(\s*", "(", name)
    name = re.sub(r"\s*\)", ")", name)
    name = re.sub(r"\s+-\s+", " – ", name)
    name = re.sub(r"\s{2,}", " ", name).rstrip(" .")
    name = re.sub(r"(?i)\bcontre[ -]angle(s?)\b", r"contre-angle\1", name)
    name = re.sub(r"(\d) (\d{3})\b", "\\1\u202f\\2", name)   # 40 000 stays together
    name = re.sub(r"(?i)\btours\s*/\s*mn\b", "tr/min", name)

    words = name.split(" ")
    out = []
    for i, w in enumerate(words):
        core = w.strip("()")
        up = core.upper()
        if up in FIXED_WORDS:
            new = FIXED_WORDS[up]
        elif up in TYPOS:
            new = TYPOS[up]
        elif core.isupper() and len(core) > 1 and core.isalpha():
            new = core.lower()
        else:
            new = core
        out.append(w.replace(core, new) if core else w)
    name = " ".join(out)
    # sentence case on the first letter
    return name[:1].upper() + name[1:] if name else name


def lot_label(lot_size: int) -> str:
    return "À l'unité" if lot_size == 1 else f"Lot de {lot_size} unités"


def format_price(value: Decimal) -> str:
    """French formatting: 4709.99 -> '4 709,99 €' (non-breaking spaces)."""
    q = value.quantize(Decimal("0.01"))
    units, cents = f"{q:.2f}".split(".")
    groups = []
    while units:
        groups.insert(0, units[-3:])
        units = units[:-3]
    return " ".join(groups) + "," + cents + " €"


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #
def normalize_products_json(payload: dict, store_domain: str = STORE_DOMAIN) -> list[Product]:
    products: list[Product] = []
    for p in payload.get("products", []):
        variants = p.get("variants") or []
        if not variants:
            continue
        # One post per variant would multiply near-identical posts; the catalogue
        # uses one product per lot, so we take the first *available* variant.
        variant = next((v for v in variants if v.get("available")), variants[0])
        warnings = []
        try:
            price = parse_price(variant.get("price"))
        except CatalogError as exc:
            warnings.append(str(exc))
            price = Decimal("0")
        cap = variant.get("compare_at_price")
        compare_at = Decimal(str(cap)) if cap not in (None, "", "0.00") else None

        sku = (variant.get("sku") or "").strip()
        if not sku:
            warnings.append("SKU vide dans Shopify (référence = handle)")
        title = p.get("title", "").strip()
        lot = parse_lot_size(title)
        handle_num = re.match(r"^(\d+)-", p.get("handle", ""))
        if handle_num and int(handle_num.group(1)) != lot:
            warnings.append(f"handle indique {handle_num.group(1)}, titre indique lot de {lot}")

        images = p.get("images") or []
        products.append(Product(
            id=str(p.get("id")),
            handle=p.get("handle", ""),
            title=title,
            display_name=clean_name(title),
            reference=sku or p.get("handle", ""),
            reference_is_sku=bool(sku),
            price=price,
            compare_at_price=compare_at,
            lot_size=lot,
            category=detect_category(title, p.get("product_type", "")),
            available=bool(variant.get("available")),
            image_url=images[0]["src"] if images else None,
            url=f"https://{store_domain}/products/{p.get('handle', '')}",
            image_urls=[i["src"] for i in images],
            warnings=warnings,
        ))
    _flag_duplicates(products)
    return products


def _flag_duplicates(products: list[Product]) -> None:
    seen: dict[tuple, Product] = {}
    for p in products:
        key = (p.display_name.lower(), p.lot_size, p.price)
        if key in seen:
            p.warnings.append(f"doublon probable de {seen[key].handle}")
        else:
            seen[key] = p


def publishable(products: Iterable[Product]) -> list[Product]:
    """Only products that are safe to publish: available, real price, image."""
    out = []
    for p in products:
        if not p.available or p.price <= 0 or not p.image_url:
            continue
        if any(w.startswith("doublon") for w in p.warnings):
            continue
        out.append(p)
    return out


# --------------------------------------------------------------------------- #
# Network
# --------------------------------------------------------------------------- #
def fetch_products_json(store_domain: str = STORE_DOMAIN, timeout: int = 30) -> dict:
    all_products, page = [], 1
    from . import net
    while True:
        r = net.get(f"https://{store_domain}/products.json",
                    params={"limit": 250, "page": page}, timeout=timeout)
        batch = r.json().get("products", [])
        all_products.extend(batch)
        if len(batch) < 250:
            break
        page += 1
    return {"products": all_products}


STOREFRONT_QUERY = """
query($cursor: String) {
  products(first: 100, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id handle title productType tags
      images(first: 1) { nodes { url } }
      variants(first: 10) { nodes {
        id title sku availableForSale
        price { amount } compareAtPrice { amount }
      } }
    }
  }
}"""


def fetch_storefront(store_domain: str, token: str, api_version: str = "2025-10") -> dict:
    """Same output shape as products.json, via the Storefront GraphQL API."""
    url = f"https://{store_domain}/api/{api_version}/graphql.json"
    headers = {"X-Shopify-Storefront-Access-Token": token, "Content-Type": "application/json"}
    out, cursor = [], None
    while True:
        r = requests.post(url, json={"query": STOREFRONT_QUERY, "variables": {"cursor": cursor}},
                          headers=headers, timeout=30)
        r.raise_for_status()
        body = r.json()
        if body.get("errors"):
            raise CatalogError(f"Storefront API: {body['errors']}")
        data = body["data"]["products"]
        for n in data["nodes"]:
            out.append({
                "id": n["id"], "handle": n["handle"], "title": n["title"],
                "product_type": n.get("productType", ""), "tags": n.get("tags", []),
                "images": [{"src": i["url"]} for i in n["images"]["nodes"]],
                "variants": [{
                    "id": v["id"], "title": v["title"], "sku": v.get("sku") or "",
                    "available": v["availableForSale"], "price": v["price"]["amount"],
                    "compare_at_price": (v.get("compareAtPrice") or {}).get("amount"),
                } for v in n["variants"]["nodes"]],
            })
        if not data["pageInfo"]["hasNextPage"]:
            break
        cursor = data["pageInfo"]["endCursor"]
    return {"products": out}


def load_catalog(store_domain: str = STORE_DOMAIN, storefront_token: str | None = None) -> list[Product]:
    payload = (fetch_storefront(store_domain, storefront_token) if storefront_token
               else fetch_products_json(store_domain))
    return normalize_products_json(payload, store_domain)
