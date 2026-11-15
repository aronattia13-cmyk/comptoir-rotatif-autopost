"""WhatsApp click-to-chat links (https://wa.me/<number>?text=...).

The number is never hard-coded: it comes from the WHATSAPP_NUMBER secret /
environment variable, international format (+33612345678 or 33612345678).
"""
from __future__ import annotations

import os
import re
from urllib.parse import quote

DEFAULT_MESSAGES = {
    "gravure": "Bonjour, je souhaite des renseignements sur la gravure personnalisée des instruments dentaires.",
    "general": "Bonjour, je souhaite des renseignements sur vos instruments rotatifs.",
}


class WhatsAppConfigError(ValueError):
    pass


def normalize_number(raw: str | None) -> str:
    if not raw or not raw.strip():
        raise WhatsAppConfigError("WHATSAPP_NUMBER non renseigné")
    s = raw.strip()
    if s.startswith("00"):
        s = s[2:]
    digits = re.sub(r"[\s.\-()+]", "", s)
    if not digits.isdigit():
        raise WhatsAppConfigError("WHATSAPP_NUMBER doit contenir uniquement des chiffres (format international)")
    if digits.startswith("0"):
        raise WhatsAppConfigError("format national détecté : utiliser l'indicatif pays (ex. 33612345678)")
    if not 8 <= len(digits) <= 15:
        raise WhatsAppConfigError("longueur invalide pour un numéro international (8 à 15 chiffres)")
    return digits


def build_link(number: str | None = None, message: str | None = None, topic: str = "gravure") -> str:
    digits = normalize_number(number if number is not None else os.environ.get("WHATSAPP_NUMBER"))
    text = message if message is not None else DEFAULT_MESSAGES[topic]
    return f"https://wa.me/{digits}?text={quote(text, safe='')}"


def link_or_none(topic: str = "gravure") -> str | None:
    """For captions: None when the number is not configured (never invented)."""
    try:
        return build_link(topic=topic)
    except WhatsAppConfigError:
        return None
