"""Blog article retrieval from the Shopify store.

Discovery: sitemap_blogs_1.xml lists the blogs, each blog exposes an Atom feed
(/blogs/<handle>.atom). Article images come from sitemap_articles_1.xml.
Only real articles are returned; each URL is checked (HTTP 200) before use.
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict

import requests

from . import net
from .catalog import STORE_DOMAIN

ATOM = "{http://www.w3.org/2005/Atom}"
SM = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
IMG = "{http://www.google.com/schemas/sitemap-image/1.1}"


@dataclass
class Article:
    id: str
    title: str
    url: str
    published: str
    summary: str               # plain text, taken verbatim from the article
    image_url: str | None
    blog: str
    url_ok: bool | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def handle(self) -> str:
        return self.url.rstrip("/").rsplit("/", 1)[-1]

    def to_dict(self):
        return asdict(self) | {"handle": self.handle}


class BlogFetchError(RuntimeError):
    pass


def _text(raw: str) -> str:
    """HTML -> clean plain text (feeds are double-escaped)."""
    t = html.unescape(html.unescape(raw or ""))
    t = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s*Plus\s*\]*>?\s*$", "", " ".join(t.split()))   # Shopify "Plus" teaser
    return t.strip()


def first_sentences(text: str, max_chars: int = 220) -> str:
    """Verbatim excerpt cut on a sentence boundary (never rewritten)."""
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    end = max(cut.rfind(". "), cut.rfind("… "), cut.rfind("! "), cut.rfind("? "))
    if end > 60:
        return cut[: end + 1]
    return cut.rsplit(" ", 1)[0] + "…"


def parse_atom(xml_text: str, blog: str) -> list[Article]:
    root = ET.fromstring(xml_text)
    out = []
    for e in root.findall(f"{ATOM}entry"):
        link = e.find(f"{ATOM}link[@rel='alternate']")
        if link is None:
            link = e.find(f"{ATOM}link")
        url = link.get("href") if link is not None else ""
        summary = _text(e.findtext(f"{ATOM}summary", ""))
        content = _text(e.findtext(f"{ATOM}content", ""))
        a = Article(
            id=e.findtext(f"{ATOM}id", url),
            title=html.unescape(e.findtext(f"{ATOM}title", "")).strip(),
            url=url,
            published=e.findtext(f"{ATOM}published", ""),
            summary=first_sentences(summary or content),
            image_url=None,
            blog=blog,
        )
        if not summary:
            a.warnings.append("pas de résumé Shopify (extrait du contenu utilisé)")
        out.append(a)
    return out


def parse_article_images(sitemap_xml: str) -> dict[str, str]:
    root = ET.fromstring(sitemap_xml)
    images = {}
    for u in root.findall(f"{SM}url"):
        loc = u.findtext(f"{SM}loc", "")
        img = u.find(f"{IMG}image/{IMG}loc")
        if img is not None and img.text:
            images[loc.rstrip("/")] = img.text
    return images


def parse_blog_list(sitemap_xml: str) -> list[str]:
    root = ET.fromstring(sitemap_xml)
    return [u.findtext(f"{SM}loc", "").rstrip("/").rsplit("/", 1)[-1] for u in root.findall(f"{SM}url")]


def _get(url: str, timeout=30) -> requests.Response:
    return net.get(url, timeout=timeout)


def check_url(url: str) -> bool:
    try:
        return net.get(url, timeout=20).status_code == 200
    except requests.RequestException:
        return False


def fetch_articles(store_domain: str = STORE_DOMAIN, verify: bool = True) -> list[Article]:
    base = f"https://{store_domain}"
    blogs = parse_blog_list(_get(f"{base}/sitemap_blogs_1.xml").text)
    try:
        images = parse_article_images(_get(f"{base}/sitemap_articles_1.xml").text)
    except requests.RequestException:
        images = {}
    articles = []
    failures = []
    for b in blogs:
        try:
            articles += parse_atom(_get(f"{base}/blogs/{b}.atom").text, b)
        except (requests.RequestException, ET.ParseError) as exc:
            failures.append(f"{b}: {exc}")
    if failures and not articles:
        raise BlogFetchError("; ".join(failures))
    for a in articles:
        a.image_url = images.get(a.url.rstrip("/"))
        if verify:
            a.url_ok = check_url(a.url)
            if not a.url_ok:
                a.warnings.append("URL injoignable")
    return articles


def from_dicts(rows: list[dict]) -> list[Article]:
    keys = Article.__dataclass_fields__
    return [Article(**{k: v for k, v in r.items() if k in keys}) for r in rows]


def usable(articles: list[Article]) -> list[Article]:
    return [a for a in articles if a.url and a.title and a.url_ok is not False]
