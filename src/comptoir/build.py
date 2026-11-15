"""Turn plan items into finished publications: JPEG + captions.
Graphic system: Direction A « Clinique » (comptoir.design)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from PIL import Image

from . import captions, design, media, whatsapp
from .blog import Article
from .catalog import Product
from .planner import PlanItem

ROOT = Path(__file__).resolve().parents[2]
MEDIA_DIR = ROOT / "site" / "media"


def _open(url: str) -> Image.Image:
    if url.startswith(("http://", "https://")):
        return media.download_image(url)
    return Image.open(url)


def product_shot(p: Product, need_cutout: bool) -> design.Shot:
    """Best real photograph of the product (sharpest full-product shot)."""
    imgs = []
    for u in p.image_urls or [p.image_url]:
        try:
            imgs.append(_open(u))
        except Exception:
            continue
    if not imgs:
        raise RuntimeError(f"aucune photo téléchargeable pour {p.handle}")
    return design.prepare(imgs[design.choose_photo(imgs)], need_cutout)


def save_jpeg(img: Image.Image, path: Path) -> Path:
    """Instagram accepts JPEG only (baseline, sRGB)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(path, "JPEG", quality=94, subsampling=0, optimize=True)
    return path


def build_item(item: PlanItem, products: list[Product], articles: list[Article], logo=None,
               post_format: str = "portrait", out_dir: Path = MEDIA_DIR) -> PlanItem:
    if item.status != "planned":
        return item
    story = item.surface == "story"
    seed = item.uid

    if item.family == "product":
        p = next(x for x in products if x.handle == item.reference)
        shot = product_shot(p, need_cutout=item.layout == "a-brume")
        img = design.product_story(p, shot, item.layout) if story else design.product_post(p, shot, item.layout)
        ig = captions.product_caption(p, seed, "instagram")
        fb = captions.product_caption(p, seed, "facebook")
    elif item.family == "blog":
        a = next(x for x in articles if x.url == item.url)
        shot = design.prepare(_open(item.photo_url), False) if item.photo_url else None
        img = design.blog_story(item.title, shot) if story else design.blog_post(item.title, shot)
        recent = a.published and (date.fromisoformat(item.date) - date.fromisoformat(a.published[:10])).days <= 30
        ig = captions.blog_caption(a, item.title, seed, "instagram", bool(recent))
        fb = captions.blog_caption(a, item.title, seed, "facebook", bool(recent))
    else:
        # Real engraving photo only if provided; otherwise purely typographic.
        shot = design.prepare(_open(item.photo_url), False) if item.photo_source == "service" else None
        img = (design.service_story(item.title, item.body, item.layout, shot) if story
               else design.service_post(item.title, item.body, item.layout, shot))
        msg = {"title": item.title, "body": item.body}
        wa = whatsapp.link_or_none(topic="gravure")
        ig = captions.service_caption(msg, "instagram", wa)
        fb = captions.service_caption(msg, "facebook", wa)
        item.payload["whatsapp_link"] = wa
        if item.facebook:                       # Facebook feed post: link clickable in caption
            fb_img = design.service_post(item.title, item.body, item.layout, shot)
            fb_path = save_jpeg(fb_img, out_dir / f"{item.uid}-fb.jpg")
            item.payload["facebook_image"] = str(fb_path.relative_to(ROOT))

    path = save_jpeg(img, out_dir / f"{item.uid}.jpg")
    item.payload["image"] = str(path.relative_to(ROOT))
    item.payload["caption_instagram"] = "" if story else ig
    item.payload["caption_facebook"] = fb if item.facebook else ""
    item.payload["format"] = "story" if story else post_format
    item.payload["design"] = "A"
    return item
