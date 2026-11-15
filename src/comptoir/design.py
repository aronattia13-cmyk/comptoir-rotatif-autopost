"""Graphic system — Direction A « Clinique » (approved 2026-11-15).

Principles
- White or very light neutral background, generous margins, one grid.
- The product is the hero: one real photograph, never redrawn or upscaled
  beyond MAX_UPSCALE (sharpness over size).
- Logo: original file, small, top-left (posts) / top-centre (stories). Never enlarged.
- Typography: Inter Tight (titles, prices, labels). Instrument Serif is used
  sparingly as a discreet accent (blog titles, engraving, one product variant).
- Bottom line on posts: name + specs on the left, lot + price on the right,
  sharing one baseline. Blog and engraving reuse that exact slot for a quiet
  text invitation ("Lire l'article"), never a drawn button.

Formats: post 1080×1350 (4:5) · story 1080×1920 (dedicated vertical layouts,
content kept inside Instagram's safe zone).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

from .catalog import Product, format_price

ROOT = Path(__file__).resolve().parents[2]
FONTS = ROOT / "assets" / "fonts" / "da"
LOGO = ROOT / "assets" / "logo.png"

POST = (1080, 1350)
STORY = (1080, 1920)
M = 88                       # post margin
MS = 96                      # story side margin
STORY_TOP = 236              # Instagram story safe zone (top UI)
STORY_BOTTOM = 1590          # last baseline in stories (bottom UI below)
MAX_UPSCALE = 1.2

WHITE = (255, 255, 255)
MIST = (245, 245, 243)       # very light warm grey
INK = (24, 27, 32)
GREY = (122, 126, 133)

PRODUCT_VARIANTS = ["a-blanc", "a-brume", "a-blanc-serif"]
BLOG_VARIANTS = ["a-blog"]
SERVICE_VARIANTS = ["a-gravure-blanc", "a-gravure-brume"]


# --------------------------------------------------------------- fonts ----
def sans(weight: int, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / f"inter-tight-latin-{weight}-normal.woff2"), size)


def serif(size: int, italic: bool = False) -> ImageFont.FreeTypeFont:
    style = "italic" if italic else "normal"
    return ImageFont.truetype(str(FONTS / f"instrument-serif-latin-400-{style}.woff2"), size)


def label(d, xy, text, size, fill=GREY, tracking=None, anchor="ls"):
    """Small capitals with letter-spacing (labels: LOT DE 5, LIRE L'ARTICLE)."""
    f = sans(500, size)
    text = text.upper()
    tr = tracking if tracking is not None else size * 0.17
    width = sum(f.getlength(c) for c in text) + tr * (len(text) - 1)
    x, y = xy
    if anchor[0] == "r":
        x -= width
    elif anchor[0] == "m":
        x -= width / 2
    for c in text:
        d.text((x, y), c, font=f, fill=fill, anchor="l" + anchor[1])
        x += f.getlength(c) + tr
    return width


def wrap(text: str, f, max_w: float, max_lines: int = 2) -> list[str]:
    """Fewest lines that fit, then the most even split (no orphan words).
    Returns more than max_lines lines only if the text cannot fit."""
    words = [w for w in text.split(" ") if w]
    n = len(words)
    from functools import lru_cache
    from itertools import combinations
    width = lru_cache(None)(lambda i, j: f.getlength(" ".join(words[i:j])))
    for k in range(1, min(max_lines, n) + 1):
        best, best_cost = None, None
        for cuts in combinations(range(1, n), k - 1):
            b = (0, *cuts, n)
            ws = [width(b[i], b[i + 1]) for i in range(k)]
            if max(ws) > max_w:
                continue
            cost = (max(ws), -ws[-1])          # narrowest block, avoid a short last line
            if best_cost is None or cost < best_cost:
                best, best_cost = b, cost
        if best:
            return [" ".join(words[best[i]:best[i + 1]]) for i in range(k)]
    # greedy fallback (overflow: caller shrinks the size)
    lines, cur = [], ""
    for w in words:
        t = f"{cur} {w}".strip()
        if f.getlength(t) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    return lines + [cur]


def fit_lines(text, face, sizes, max_w, max_lines=2):
    """Largest size from `sizes` where text fits in max_lines."""
    for s in sizes:
        f = face(s)
        lines = wrap(text, f, max_w, max_lines)
        if len(lines) <= max_lines and all(f.getlength(l) <= max_w for l in lines):
            return f, lines
    f = face(sizes[-1])
    return f, wrap(text, f, max_w, max_lines)[:max_lines]


# --------------------------------------------------------------- names ----
def split_name(name: str) -> tuple[str, str]:
    """Typographic hierarchy, every word kept and in order.
    'Contre-angle bague bleue 1:1 40 000 tr/min LED simple spray'
        -> ('Contre-angle bague bleue 1:1', '40 000 tr/min · LED · simple spray')
    'Turbine raccord rapide KaVo – LED triple spray'
        -> ('Turbine raccord rapide KaVo', 'LED · triple spray')
    'Détartreur compatible Satelec DTE avec LED'
        -> ('Détartreur compatible Satelec DTE', 'avec LED')
    """
    name = re.sub(r" {2,}", " ", name.strip())
    m = re.search(r"\b\d+:\d+\b", name)
    if m:
        title, rest = name[: m.end()], name[m.end():].strip()
        pm = re.match(r"^(\([^)]*\))\s*(.*)$", rest)
        if pm:
            title, rest = f"{title} {pm.group(1)}", pm.group(2)
    elif " – " in name:
        title, rest = name.split(" – ", 1)
    else:
        m = re.search(r"\s(avec|sans)\s", name)
        title, rest = (name[: m.start()], name[m.start() + 1:]) if m else (name, "")
    # "LED" as its own spec item, unless it reads "avec LED" / "sans LED"
    items, cur = [], []
    for tok in [t for t in rest.split(" ") if t]:
        if tok == "LED" and not (cur and cur[-1] in ("avec", "sans")):
            if cur:
                items.append(" ".join(cur))
            items.append("LED")
            cur = []
        else:
            cur.append(tok)
    if cur:
        items.append(" ".join(cur))
    rest = " · ".join(items)
    return title.strip(), re.sub(r"\s{2,}", " ", rest).strip()


def lot_text(n: int) -> str:
    return "À l'unité" if n == 1 else f"Lot de {n}"


# --------------------------------------------------------------- photo ----
def _flood_background(rgb: Image.Image, tol: int) -> np.ndarray:
    a = np.asarray(rgb).astype(np.int16)
    cand = Image.fromarray(((255 - a.min(axis=2) <= tol) * 255).astype(np.uint8)).copy()
    w, h = cand.size
    seeds = [(x, 0) for x in range(0, w, 5)] + [(x, h - 1) for x in range(0, w, 5)] + \
            [(0, y) for y in range(0, h, 5)] + [(w - 1, y) for y in range(0, h, 5)]
    for s in seeds:
        if cand.getpixel(s) == 255:
            ImageDraw.floodfill(cand, s, 128)
    return np.asarray(cand) == 128


def _box_blur(x: np.ndarray, r: int = 6) -> np.ndarray:
    k = 2 * r + 1
    c = np.pad(np.pad(x, r, mode="edge").cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    return (c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]) / (k * k)


def is_white_background(img: Image.Image) -> bool:
    rgb = img.convert("RGB")
    a = np.asarray(rgb)
    border = np.concatenate([a[:6].reshape(-1, 3), a[-6:].reshape(-1, 3),
                             a[:, :6].reshape(-1, 3), a[:, -6:].reshape(-1, 3)])
    return border.min(axis=1).mean() >= 250 and np.percentile(border.min(axis=1), 2) >= 240


def cutout(img: Image.Image, tol: int = 10) -> Image.Image:
    """Alpha matte for a photo shot on white. Product pixels untouched;
    only the anti-aliased edge is un-mixed from white (no halo)."""
    rgb = ImageOps.exif_transpose(img).convert("RGB")
    a = np.asarray(rgb).astype(np.float32)
    bg = _flood_background(rgb, tol)
    obj = ~bg
    obj_im = Image.fromarray((obj * 255).astype(np.uint8))
    from PIL import ImageFilter
    inner = np.asarray(obj_im.filter(ImageFilter.MinFilter(5))) > 0
    near = np.asarray(obj_im.filter(ImageFilter.MaxFilter(3))) > 0
    band = (obj & ~inner) | (bg & near)
    inner_f = inner.astype(np.float32)
    den = _box_blur(inner_f)[..., None]
    num = np.stack([_box_blur(a[..., c] * inner_f) for c in range(3)], -1)
    objc = np.where(den > 1e-3, num / np.maximum(den, 1e-3), a)
    est = ((255 - a) / np.maximum(255 - objc, 8)).max(axis=2).clip(0, 1)
    alpha = np.where(inner, 1.0, np.where(band, est, np.where(obj, 1.0, 0.0)))
    col = np.where(band[..., None], 255 - (255 - a) / np.maximum(alpha[..., None], 0.05), a).clip(0, 255)
    im = Image.fromarray(np.dstack([col, alpha * 255]).astype(np.uint8), "RGBA")
    return im.crop(im.getchannel("A").point(lambda v: 255 if v > 10 else 0).getbbox())


def trim_white(img: Image.Image, thr: int = 248) -> Image.Image:
    rgb = img.convert("RGB")
    a = np.asarray(rgb).min(axis=2)
    ys, xs = np.where(a < thr)
    if len(xs) == 0:
        return rgb
    return rgb.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))


@dataclass
class Shot:
    """A product photograph ready for layout."""
    image: Image.Image          # RGB trimmed (white bg) or RGBA cut-out
    native: tuple[int, int]     # trimmed size in source pixels (upscale limit)
    on_white: bool


def prepare(img: Image.Image, need_cutout: bool) -> Shot:
    img = ImageOps.exif_transpose(img)
    if img.mode == "RGBA" and img.getchannel("A").getextrema()[0] < 250:
        im = img.crop(img.getchannel("A").getbbox())
        return Shot(im, im.size, True)
    if not is_white_background(img):
        rgb = img.convert("RGB")
        return Shot(rgb, rgb.size, False)
    if need_cutout:
        c = cutout(img)
        return Shot(c, c.size, True)
    t = trim_white(img)
    return Shot(t, t.size, True)


def choose_photo(images: list[Image.Image]) -> int:
    """Index of the best full-product shot: the main image (index 0), or a
    sharper white-background version of the same framing (same aspect ratio
    of the trimmed product, higher resolution)."""
    if not images:
        raise ValueError("aucune photo")
    ref = trim_white(images[0])
    ref_ratio = ref.width / ref.height
    best, best_h = 0, ref.height
    for i, im in enumerate(images[1:], 1):
        if not is_white_background(im):
            continue
        t = trim_white(im)
        if abs(t.width / t.height - ref_ratio) / ref_ratio < 0.12 and t.height > best_h * 1.3:
            best, best_h = i, t.height
    return best


def paste_shot(canvas: Image.Image, shot: Shot, box: tuple[int, int, int, int], align="center"):
    x0, y0, x1, y1 = box
    im = shot.image
    k = min((x1 - x0) / im.width, (y1 - y0) / im.height, MAX_UPSCALE * shot.native[0] / im.width)
    nw, nh = round(im.width * k), round(im.height * k)
    im = im.resize((nw, nh), Image.LANCZOS)
    x = x0 + ((x1 - x0) - nw) // 2 if align == "center" else x0
    y = y0 + ((y1 - y0) - nh) // 2
    if im.mode == "RGBA":
        canvas.paste(im, (x, y), im)
    else:
        canvas.paste(im, (x, y))
    return (x, y, x + nw, y + nh)


def logo(width: int) -> Image.Image:
    lg = Image.open(LOGO).convert("RGBA")
    lg = lg.crop(lg.getchannel("A").getbbox())
    width = min(width, lg.width)                     # never enlarged
    k = width / lg.width
    return lg.resize((round(lg.width * k), round(lg.height * k)), Image.LANCZOS)


# ------------------------------------------------------------- products ----
def product_post(p: Product, shot: Shot, variant: str = "a-blanc") -> Image.Image:
    W, H = POST
    bg = MIST if variant == "a-brume" and shot.image.mode == "RGBA" else WHITE
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    lg = logo(170)
    img.paste(lg, (M, M), lg)

    title, spec = split_name(p.display_name)
    price = format_price(p.price)
    base = H - M
    f_price = sans(500, 52)
    price_w = f_price.getlength(price)
    col_w = W - 2 * M - price_w - 64
    face = (lambda s: serif(s)) if variant == "a-blanc-serif" else (lambda s: sans(500, s))
    sizes = [50, 48, 46, 44] if variant == "a-blanc-serif" else [40, 38, 36, 34]
    f_t, lines = fit_lines(title, face, sizes, col_w, 2)
    f_s = sans(400, 25)
    lh = round(f_t.size * (1.08 if variant == "a-blanc-serif" else 1.16))

    spec_lines = wrap(spec, f_s, col_w, 2) if spec else []
    y = base - 34 * (len(spec_lines) - 1)
    for sl in spec_lines:
        d.text((M, y), sl, font=f_s, fill=GREY, anchor="ls")
        y += 34
    t_base = base - 34 * len(spec_lines) - (10 if spec_lines else 0)
    if not spec_lines:
        t_base = base
    for i, line in enumerate(reversed(lines)):
        d.text((M, t_base - i * lh), line, font=f_t, fill=INK, anchor="ls")
    text_top = t_base - (len(lines) - 1) * lh - f_t.size

    d.text((W - M, base), price, font=f_price, fill=INK, anchor="rs")
    label(d, (W - M, base - 66), lot_text(p.lot_size), 19, anchor="rs")

    top = M + lg.height + 48
    bottom = min(text_top, base - 66 - 19) - 56
    paste_shot(img, shot, (M + 40, top, W - M - 40, bottom))
    return img


def product_story(p: Product, shot: Shot, variant: str = "a-blanc") -> Image.Image:
    W, H = STORY
    bg = MIST if variant == "a-brume" and shot.image.mode == "RGBA" else WHITE
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    lg = logo(196)
    img.paste(lg, ((W - lg.width) // 2, STORY_TOP), lg)
    title, _ = split_name(p.display_name)
    price = format_price(p.price)
    base = STORY_BOTTOM
    lot = lot_text(p.lot_size)
    f_lot, f_pr = sans(400, 32), sans(500, 42)
    gap = 36
    wl, wp = f_lot.getlength(lot), f_pr.getlength(price)
    x0 = W / 2 - (wl + gap + wp) / 2
    d.text((x0, base), lot, font=f_lot, fill=GREY, anchor="ls")
    d.text((x0 + wl + gap, base), price, font=f_pr, fill=INK, anchor="ls")
    face = (lambda s: serif(s)) if variant == "a-blanc-serif" else (lambda s: sans(500, s))
    sizes = [60, 56, 52] if variant == "a-blanc-serif" else [48, 46, 44, 42]
    f_t, lines = fit_lines(title, face, sizes, W - 2 * MS - 40, 2)
    lh = round(f_t.size * 1.14)
    t_base = base - 78
    for i, line in enumerate(reversed(lines)):
        d.text((W / 2, t_base - i * lh), line, font=f_t, fill=INK, anchor="ms")
    text_top = t_base - (len(lines) - 1) * lh - f_t.size
    paste_shot(img, shot, (MS + 60, STORY_TOP + lg.height + 80, W - MS - 60, text_top - 80))
    return img


# ----------------------------------------------------------------- blog ----
def blog_post(title: str, shot: Shot | None, variant: str = "a-blog") -> Image.Image:
    W, H = POST
    img = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.Draw(img)
    lg = logo(170)
    img.paste(lg, (M, M), lg)
    label(d, (W - M, M + lg.height // 2 + 7), "Le blog", 19, anchor="rs")
    base = H - M
    cta_w = label(d, (W - M, base), "Lire l'article", 19, fill=INK, anchor="rs")
    f_t, lines = fit_lines(title, serif, [56, 52, 48, 44], W - 2 * M - cta_w - 72, 3)
    lh = round(f_t.size * 1.06)
    for i, line in enumerate(reversed(lines)):
        d.text((M, base - i * lh), line, font=f_t, fill=INK, anchor="ls")
    text_top = base - (len(lines) - 1) * lh - f_t.size
    if shot:
        paste_shot(img, shot, (M + 40, M + lg.height + 48, W - M - 40, text_top - 64))
    return img


def blog_story(title: str, shot: Shot | None) -> Image.Image:
    W, H = STORY
    img = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.Draw(img)
    lg = logo(196)
    img.paste(lg, ((W - lg.width) // 2, STORY_TOP), lg)
    base = STORY_BOTTOM
    label(d, (W / 2, base), "Lire l'article · lien en bio", 22, fill=INK, anchor="ms")
    f_t, lines = fit_lines(title, serif, [68, 64, 60, 56], W - 2 * MS - 40, 3)
    lh = round(f_t.size * 1.06)
    t_base = base - 86
    for i, line in enumerate(reversed(lines)):
        d.text((W / 2, t_base - i * lh), line, font=f_t, fill=INK, anchor="ms")
    text_top = t_base - (len(lines) - 1) * lh - f_t.size
    if shot:
        paste_shot(img, shot, (MS + 60, STORY_TOP + lg.height + 80, W - MS - 60, text_top - 80))
    return img


# ------------------------------------------------------------ engraving ----
def service_post(title: str, body: str, variant: str = "a-gravure-blanc",
                 shot: Shot | None = None, cta: str = "Renseignements · lien en bio") -> Image.Image:
    """Typographic by default. A real photo (assets/services/gravure/) is used
    only when provided — never a simulated engraving."""
    W, H = POST
    bg = MIST if variant == "a-gravure-brume" else WHITE
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    lg = logo(170)
    img.paste(lg, (M, M), lg)
    label(d, (W - M, M + lg.height // 2 + 7), "Service", 19, anchor="rs")
    base = H - M
    label(d, (M, base), cta, 19, fill=INK)
    f_b = sans(400, 30)
    b_lines = wrap(body, f_b, W - 2 * M - 120, 3)
    f_t, t_lines = fit_lines(title, serif, [96, 88, 80, 72], W - 2 * M, 3)
    lh = round(f_t.size * 1.02)
    if shot:
        y = base - 110
        for line in reversed(b_lines):
            d.text((M, y), line, font=f_b, fill=GREY, anchor="ls")
            y -= 42
        y -= 30
        for line in reversed(t_lines):
            d.text((M, y), line, font=f_t, fill=INK, anchor="ls")
            y -= lh
        paste_shot(img, shot, (M + 40, M + lg.height + 48, W - M - 40, y - 40))
    else:
        # title block sits on the optical centre, body below, generous air
        block = len(t_lines) * lh + 40 + len(b_lines) * 42
        y = (H - block) // 2 + f_t.size // 2
        for line in t_lines:
            d.text((M - 3, y), line, font=f_t, fill=INK, anchor="ls")
            y += lh
        y += 40 - lh + 42
        for line in b_lines:
            d.text((M, y), line, font=f_b, fill=GREY, anchor="ls")
            y += 42
    return img


def service_story(title: str, body: str, variant: str = "a-gravure-blanc",
                  shot: Shot | None = None, cta: str = "Renseignements · lien en bio") -> Image.Image:
    W, H = STORY
    bg = MIST if variant == "a-gravure-brume" else WHITE
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    lg = logo(196)
    img.paste(lg, (MS, STORY_TOP), lg)
    base = STORY_BOTTOM
    label(d, (MS, base), cta, 22, fill=INK)
    f_b = sans(400, 36)
    b_lines = wrap(body, f_b, W - 2 * MS - 80, 3)
    f_t, t_lines = fit_lines(title, serif, [124, 112, 104, 96], W - 2 * MS, 3)
    lh = round(f_t.size * 1.0)
    if shot:
        y = base - 120
        for line in reversed(b_lines):
            d.text((MS, y), line, font=f_b, fill=GREY, anchor="ls")
            y -= 50
        y -= 36
        for line in reversed(t_lines):
            d.text((MS, y), line, font=f_t, fill=INK, anchor="ls")
            y -= lh
        paste_shot(img, shot, (MS + 40, STORY_TOP + lg.height + 80, W - MS - 40, y - 40))
    else:
        block = len(t_lines) * lh + 48 + len(b_lines) * 50
        y = (STORY_TOP + base) // 2 - block // 2 + f_t.size // 2
        for line in t_lines:
            d.text((MS - 4, y), line, font=f_t, fill=INK, anchor="ls")
            y += lh
        y += 48 - lh + 50
        for line in b_lines:
            d.text((MS, y), line, font=f_b, fill=GREY, anchor="ls")
            y += 50
    return img
