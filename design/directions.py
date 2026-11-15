"""Art-direction exploration — 3 directions for ONE product (post 4:5 + story 9:16).

Isolated from the publishing system: nothing here is used by the planner or
the workflows until a direction is approved.

A · Clinique   pure white, product as hero, sans-serif, strict baseline grid
B · Éditorial  warm stone background, serif title, asymmetric catalogue layout
C · Détail     real macro photograph, quiet light-grey type area
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from comptoir import catalog, media  # noqa: E402

F = ROOT / "assets" / "fonts" / "da"
OUT = ROOT / "site" / "da"

INK = (24, 27, 32)
GREY = (122, 126, 133)
STONE = (238, 234, 228)
STONE_INK = (36, 33, 30)
STONE_GREY = (128, 121, 112)
MIST = (243, 243, 241)


def sans(w, size):
    return ImageFont.truetype(str(F / f"inter-tight-latin-{w}-normal.woff2"), size)


def serif(size, italic=False):
    return ImageFont.truetype(str(F / f"instrument-serif-latin-400-{'italic' if italic else 'normal'}.woff2"), size)


# ----------------------------------------------------------------- photo ----
def trim_white(img: Image.Image, thr=248, pad=0) -> Image.Image:
    a = np.asarray(img.convert("RGB")).min(axis=2)
    ys, xs = np.where(a < thr)
    box = (max(xs.min() - pad, 0), max(ys.min() - pad, 0),
           min(xs.max() + pad + 1, img.width), min(ys.max() + pad + 1, img.height))
    return img.crop(box)


def _blur(x: np.ndarray, r: int = 6) -> np.ndarray:
    """Box blur (float) via integral image."""
    k = 2 * r + 1
    p = np.pad(x, r, mode="edge")
    c = p.cumsum(0).cumsum(1)
    c = np.pad(c, ((1, 0), (1, 0)))
    return (c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]) / (k * k)


def cutout_on_white(img: Image.Image, tol=10) -> Image.Image:
    """Alpha matte for a photo shot on pure white. Product pixels untouched;
    only the anti-aliased edge band is un-mixed from white (no halo)."""
    rgb = img.convert("RGB")
    a = np.asarray(rgb).astype(np.float32)
    dist = 255 - a.min(axis=2)
    cand = Image.fromarray(((dist <= tol) * 255).astype(np.uint8)).copy()  # writable copy
    w, h = cand.size
    for s in [(x, 0) for x in range(0, w, 5)] + [(x, h - 1) for x in range(0, w, 5)] + \
             [(0, y) for y in range(0, h, 5)] + [(w - 1, y) for y in range(0, h, 5)]:
        if cand.getpixel(s) == 255:
            ImageDraw.floodfill(cand, s, 128)
    bg = np.asarray(cand) == 128
    obj = ~bg
    inner = np.asarray(Image.fromarray((obj * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(5))) > 0
    band = obj & ~inner | (bg & (np.asarray(Image.fromarray((obj * 255).astype(np.uint8))
                                            .filter(ImageFilter.MaxFilter(3))) > 0))
    # local colour of the product just inside the edge
    inner_f = inner.astype(np.float32)
    num = np.stack([_blur(a[..., c] * inner_f) for c in range(3)], -1)
    den = _blur(inner_f)[..., None]
    objc = np.where(den > 1e-3, num / np.maximum(den, 1e-3), a)
    alpha = np.where(obj, 1.0, 0.0)
    est = ((255 - a) / np.maximum(255 - objc, 8)).max(axis=2).clip(0, 1)
    alpha = np.where(band, est, alpha)
    alpha = np.where(inner, 1.0, alpha)
    col = np.where(band[..., None],
                   255 - (255 - a) / np.maximum(alpha[..., None], 0.05), a).clip(0, 255)
    out = np.dstack([col, alpha * 255]).astype(np.uint8)
    im = Image.fromarray(out, "RGBA")
    return im.crop(im.getchannel("A").point(lambda v: 255 if v > 10 else 0).getbbox())


def fit_h(img, max_w, max_h):
    k = min(max_w / img.width, max_h / img.height)
    return img.resize((round(img.width * k), round(img.height * k)), Image.LANCZOS)


def cover(img, w, h, cx=0.5, cy=0.5):
    k = max(w / img.width, h / img.height)
    im = img.resize((round(img.width * k), round(img.height * k)), Image.LANCZOS)
    x = round((im.width - w) * cx)
    y = round((im.height - h) * cy)
    return im.crop((x, y, x + w, y + h))


# ------------------------------------------------------------------ type ----
def tracked(d, xy, text, f, fill, tracking, anchor="ls"):
    x, y = xy
    total = sum(f.getlength(c) for c in text) + tracking * (len(text) - 1)
    if anchor[0] == "r":
        x -= total
    elif anchor[0] == "m":
        x -= total / 2
    for c in text:
        d.text((x, y), c, font=f, fill=fill, anchor="l" + anchor[1])
        x += f.getlength(c) + tracking
    return total


def logo_img(width):
    lg = Image.open(ROOT / "assets" / "logo.png").convert("RGBA")
    lg = lg.crop(lg.getchannel("A").getbbox())
    k = width / lg.width
    return lg.resize((round(lg.width * k), round(lg.height * k)), Image.LANCZOS)


def split_name(name: str) -> tuple[str, str]:
    """'Contre-angle bague bleue 1:1 40 000 tr/min LED simple spray'
    -> ('Contre-angle bague bleue 1:1', '40 000 tr/min · LED · simple spray')"""
    import re
    m = re.search(r"\b\d+:\d+\b", name)
    if m:
        title, rest = name[: m.end()].strip(), name[m.end():].strip()
    else:
        m = re.search(r"\s(–|-)\s|\s(LED|avec|sans)\b", name)
        title, rest = (name[: m.start()].strip(), name[m.start():].strip(" –-")) if m else (name, "")
    rest = re.sub(r"\s*\bLED\b\s*", " · LED · ", rest).strip(" ·")
    return title, re.sub(r"\s{2,}", " ", rest)


# -------------------------------------------------------------- A post ----
def a_post(p, photo):
    W, H, M = 1080, 1350, 88
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    lg = logo_img(170)
    img.paste(lg, (M, M), lg)
    title, spec = split_name(p.display_name)
    price = catalog.format_price(p.price)
    base = H - M                                         # bottom baseline
    d.text((M, base - 44), title, font=sans(500, 40), fill=INK, anchor="ls")
    d.text((M, base), spec, font=sans(400, 25), fill=GREY, anchor="ls")
    d.text((W - M, base), price, font=sans(500, 52), fill=INK, anchor="rs")
    tracked(d, (W - M, base - 66), f"LOT DE {p.lot_size}", sans(500, 19), GREY, 3.2, "rs")
    prod = fit_h(photo, 520, 860)
    top, bottom = M + lg.height + 40, base - 44 - 40 - 70
    img.paste(prod, ((W - prod.width) // 2, top + (bottom - top - prod.height) // 2))
    return img


def a_story(p, photo):
    W, H, M = 1080, 1920, 96
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    lg = logo_img(196)
    img.paste(lg, ((W - lg.width) // 2, 236), lg)
    title, _ = split_name(p.display_name)
    price = catalog.format_price(p.price)
    base = H - 330
    d.text((W / 2, base - 74), title, font=sans(500, 46), fill=INK, anchor="ms")
    lot = f"Lot de {p.lot_size}"
    f_lot, f_pr = sans(400, 32), sans(500, 40)
    gap = 36
    wl, wp = f_lot.getlength(lot), f_pr.getlength(price)
    x0 = W / 2 - (wl + gap + wp) / 2
    d.text((x0, base), lot, font=f_lot, fill=GREY, anchor="ls")
    d.text((x0 + wl + gap, base), price, font=f_pr, fill=INK, anchor="ls")
    prod = fit_h(photo, 640, 1060)
    top, bottom = 236 + lg.height + 70, base - 74 - 46 - 80
    img.paste(prod, ((W - prod.width) // 2, top + (bottom - top - prod.height) // 2))
    return img


# -------------------------------------------------------------- B post ----
def b_post(p, cut):
    W, H, M = 1080, 1350, 88
    img = Image.new("RGB", (W, H), STONE)
    d = ImageDraw.Draw(img)
    lg = logo_img(160)
    img.paste(lg, (M, M), lg)
    title, spec = split_name(p.display_name)
    t1, _, t2 = title.partition(" ")
    price = catalog.format_price(p.price)
    prod = fit_h(cut, 380, 1060)
    img.paste(prod, (W - M - prod.width - 10, (H - prod.height) // 2 + 30), prod)
    fs = serif(92)
    y = 520
    d.text((M - 4, y), t1, font=fs, fill=STONE_INK, anchor="ls")
    d.text((M - 4, y + 84), t2.replace(" 1:", "\n1:").split("\n")[0], font=fs, fill=STONE_INK, anchor="ls")
    if "1:" in t2:
        d.text((M - 4, y + 168), "1:" + t2.split("1:")[1], font=serif(92, True), fill=STONE_INK, anchor="ls")
    d.text((M, y + 230), spec, font=sans(400, 23), fill=STONE_GREY, anchor="ls")
    base = H - M
    tracked(d, (M, base - 62), f"LOT DE {p.lot_size}", sans(500, 18), STONE_GREY, 3.2)
    d.text((M, base), price, font=sans(400, 50), fill=STONE_INK, anchor="ls")
    return img


def b_story(p, cut):
    W, H, M = 1080, 1920, 96
    img = Image.new("RGB", (W, H), STONE)
    d = ImageDraw.Draw(img)
    lg = logo_img(180)
    img.paste(lg, (M, 236), lg)
    title, _ = split_name(p.display_name)
    t1, _, t2 = title.partition(" ")
    price = catalog.format_price(p.price)
    prod = fit_h(cut, 470, 1250)
    img.paste(prod, (W - M - prod.width, 400), prod)
    fs = serif(104)
    base = H - 470
    d.text((M - 4, base - 210), t1, font=fs, fill=STONE_INK, anchor="ls")
    d.text((M - 4, base - 105), t2.split(" 1:")[0], font=fs, fill=STONE_INK, anchor="ls")
    if "1:" in t2:
        d.text((M - 4, base), "1:" + t2.split("1:")[1], font=serif(104, True), fill=STONE_INK, anchor="ls")
    tracked(d, (M, base + 110), f"LOT DE {p.lot_size}", sans(500, 22), STONE_GREY, 3.5)
    d.text((M, base + 180), price, font=sans(400, 54), fill=STONE_INK, anchor="ls")
    return img


# -------------------------------------------------------------- C post ----
def c_post(p, macro):
    W, H, M = 1080, 1350, 88
    img = Image.new("RGB", (W, H), MIST)
    d = ImageDraw.Draw(img)
    ph_h = 900
    img.paste(cover(macro, W, ph_h, 0.5, 0.42), (0, 0))
    lg = logo_img(160)
    img.paste(lg, (M, M - 20), lg)
    title, spec = split_name(p.display_name)
    price = catalog.format_price(p.price)
    y0 = ph_h + 104
    d.text((M, y0), title, font=sans(400, 42), fill=INK, anchor="ls")
    d.text((M, y0 + 46), spec, font=sans(400, 24), fill=GREY, anchor="ls")
    base = H - M
    tracked(d, (M, base), f"LOT DE {p.lot_size}", sans(500, 19), GREY, 3.2)
    d.text((W - M, base), price, font=sans(300, 72), fill=INK, anchor="rs")
    return img


def c_story(p, macro):
    W, H, M = 1080, 1920, 96
    img = Image.new("RGB", (W, H), MIST)
    d = ImageDraw.Draw(img)
    ph_h = 1260
    img.paste(cover(macro, W, ph_h, 0.5, 0.45), (0, 0))
    lg = logo_img(180)
    img.paste(lg, (M, 236), lg)
    title, _ = split_name(p.display_name)
    price = catalog.format_price(p.price)
    y0 = ph_h + 130
    d.text((M, y0), title, font=sans(400, 48), fill=INK, anchor="ls")
    tracked(d, (M, y0 + 70), f"LOT DE {p.lot_size}", sans(500, 22), GREY, 3.5)
    d.text((M, H - 330), price, font=sans(300, 84), fill=INK, anchor="ls")
    return img


def main():
    prods = catalog.load_catalog()
    p = next(x for x in prods if x.handle.startswith("contre-angle-dentaire-1-1") and x.lot_size == 5)
    urls = p.image_urls
    full = media.download_image(urls[1]).convert("RGB")      # 1600 px studio shot, pure white
    macro = media.download_image(urls[2]).convert("RGB")     # 1600 px real close-up
    photo = trim_white(full, pad=0)
    cut = cutout_on_white(full)
    OUT.mkdir(parents=True, exist_ok=True)
    res = {}
    for key, fn, src in [("A_clinique_post", a_post, photo), ("A_clinique_story", a_story, photo),
                         ("B_editorial_post", b_post, cut), ("B_editorial_story", b_story, cut),
                         ("C_detail_post", c_post, macro), ("C_detail_story", c_story, macro)]:
        im = fn(p, src)
        im.save(OUT / f"{key}.jpg", quality=94, subsampling=0)
        res[key] = im
    print(p.display_name, catalog.format_price(p.price), p.lot_size)
    return res


if __name__ == "__main__":
    main()
