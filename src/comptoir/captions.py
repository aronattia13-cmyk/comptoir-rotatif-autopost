"""Caption generator — controlled phrase library, no AI.

Every sentence is either (a) a fixed, factual formula, or (b) data copied
from Shopify / the blog. Nothing technical is asserted beyond the product
title or the article's own text. Variation = deterministic choice by seed,
so a given publication always gets the same text (reproducible, testable).
"""
from __future__ import annotations

import hashlib

from .catalog import Product, format_price, lot_label
from .blog import Article

STORE_URL = "https://lecomptoirdurotatif.com"
CONTACT_URL = f"{STORE_URL}/pages/contact"

PRODUCT_OPENERS = [
    "Disponible sur Le Comptoir du Rotatif.",
    "À retrouver dans notre catalogue.",
    "Pour compléter l'équipement de votre cabinet.",
    "Une référence de notre sélection d'instruments rotatifs.",
]
LOT_LINES = {
    "single": "Prix affiché à l'unité.",
    "lot": "Prix affiché pour le lot de {n} unités.",
}
BULK_LINES = [
    "Un format adapté aux cabinets multi-praticiens et aux centres dentaires.",
    "Un volume pensé pour équiper plusieurs fauteuils.",
]
PRODUCT_CTA_IG = ["Fiche produit : lien en bio.", "Tous les détails : lien en bio."]

BLOG_OPENERS = ["Sur le blog :", "À lire sur notre blog :", "Nouvel article sur le blog :"]
BLOG_CTA_IG = ["L'article complet : lien en bio.", "À lire en entier via le lien en bio."]

SERVICE_CTA_IG = "Renseignements sur WhatsApp : lien en bio."

HASHTAGS = {
    "base": ["#chirurgiendentiste", "#cabinetdentaire", "#dentiste"],
    "turbine": ["#turbinedentaire", "#instrumentsrotatifs"],
    "contre-angle": ["#contreangle", "#instrumentsrotatifs"],
    "detartreur": ["#detartreur", "#parodontie"],
    "autre": ["#materieldentaire"],
    "blog": ["#organisationcabinet", "#materieldentaire"],
    "service": ["#gravure", "#organisationcabinet"],
}


def pick(options: list[str], seed: str) -> str:
    h = int(hashlib.sha1(seed.encode()).hexdigest(), 16)
    return options[h % len(options)]


def tags(*keys: str) -> str:
    out = []
    for k in ("base",) + keys:
        for t in HASHTAGS.get(k, []):
            if t not in out:
                out.append(t)
    return " ".join(out)


def product_caption(p: Product, seed: str, platform: str) -> str:
    lines = [p.display_name, f"{lot_label(p.lot_size)} — {format_price(p.price)}", ""]
    lines.append(pick(PRODUCT_OPENERS, seed + "o"))
    lines.append(LOT_LINES["single"] if p.lot_size == 1 else LOT_LINES["lot"].format(n=p.lot_size))
    if p.lot_size >= 50:
        lines.append(pick(BULK_LINES, seed + "b"))
    lines.append("")
    if platform == "facebook":
        lines.append(f"Fiche produit : {p.url}")
    else:
        lines.append(pick(PRODUCT_CTA_IG, seed + "c"))
        lines += ["", tags(p.category)]
    return "\n".join(lines).replace(" ", " ")


def blog_caption(a: Article, short_title: str, seed: str, platform: str, recent: bool) -> str:
    openers = BLOG_OPENERS if recent else BLOG_OPENERS[:2]
    lines = [pick(openers, seed + "o"), a.title, ""]
    if a.summary:
        lines += [f"« {a.summary} »", ""]
    if platform == "facebook":
        lines.append(f"Lire l'article : {a.url}")
    else:
        lines.append(pick(BLOG_CTA_IG, seed + "c"))
        lines += ["", tags("blog")]
    return "\n".join(lines)


def service_caption(msg: dict, platform: str, wa_link: str | None) -> str:
    lines = [msg["title"], "", msg["body"], ""]
    if platform == "facebook":
        if wa_link:
            lines.append(f"Renseignements sur WhatsApp : {wa_link}")
        else:
            lines.append(f"Renseignements : {CONTACT_URL}")
    else:
        lines.append(SERVICE_CTA_IG if wa_link else "Renseignements : lien en bio ou message privé.")
        lines += ["", tags("service")]
    return "\n".join(lines)
