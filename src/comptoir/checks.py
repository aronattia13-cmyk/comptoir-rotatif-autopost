"""Pre-publication safety checks. Any error blocks the publication."""
from __future__ import annotations

from decimal import Decimal

from .blog import check_url
from .catalog import Product
from .planner import PlanItem


def check_item(item: PlanItem, catalog: list[Product], verify_urls: bool = True) -> list[str]:
    errors: list[str] = []
    if item.status != "planned":
        return [f"statut {item.status}"]
    if not item.photo_url and item.family != "service":
        errors.append("aucune photo réelle disponible")
    if item.family == "product":
        p = next((x for x in catalog if x.handle == item.reference), None)
        if p is None:
            errors.append("produit absent du catalogue Shopify actuel")
        else:
            if not p.available:
                errors.append("produit indisponible")
            if p.price != Decimal(item.price):
                errors.append(f"prix modifié dans Shopify ({item.price} -> {p.price}) : visuel à régénérer")
            if p.lot_size != item.lot_size:
                errors.append("taille de lot modifiée")
        if verify_urls and item.url and not check_url(item.url):
            errors.append(f"fiche produit injoignable: {item.url}")
    elif item.family == "blog":
        if not item.url:
            errors.append("article sans URL")
        elif verify_urls and not check_url(item.url):
            errors.append(f"article injoignable: {item.url}")
    return errors


def scan_for_secrets(text: str, secrets: list[str]) -> bool:
    """True if any secret value leaks into a caption / file content."""
    return any(s and len(s) >= 6 and s in text for s in secrets)
