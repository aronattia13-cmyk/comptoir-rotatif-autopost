"""Direction A — demo set: 3 product posts, 2 blog posts, 2 engraving posts, 2 stories.

Usage: python scripts/demo_a.py   ->  site/demo_a/
TEST only: nothing is published, the planner is not touched.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image, ImageDraw  # noqa: E402

from comptoir import blog, catalog, design, media, planner  # noqa: E402
from comptoir.history import History  # noqa: E402

OUT = ROOT / "site" / "demo_a"


def best_shot(p, need_cutout):
    imgs = [media.download_image(u) for u in p.image_urls]
    i = design.choose_photo(imgs)
    return design.prepare(imgs[i], need_cutout), i


def save(img, name):
    path = OUT / f"{name}.jpg"
    img.save(path, "JPEG", quality=94, subsampling=0, optimize=True)
    return path


def main():
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    products = catalog.publishable(catalog.load_catalog())
    articles = blog.fetch_articles()
    services = json.loads((ROOT / "data/content/services.json").read_text())["gravure"]["messages"]
    pl = planner.Planner(products, articles, History([]))

    def find(prefix, lot):
        return next(p for p in products if p.handle.startswith(prefix) and p.lot_size == lot)

    picks = [(find("contre-angle-dentaire-1-1", 5), "a-blanc"),
             (find("10-turbines", 50), "a-brume"),
             (find("connection-satelec-dte-avec-led", 5), "a-blanc-serif")]
    paths = []
    for k, (p, variant) in enumerate(picks, 1):
        shot, idx = best_shot(p, need_cutout=variant == "a-brume")
        paths.append(save(design.product_post(p, shot, variant), f"1_produit_{k}_{variant}"))
        print(f"produit {k}: {p.display_name} | photo #{idx} {shot.native} | {variant}")

    for k, a in enumerate(sorted(blog.usable(articles), key=lambda a: a.published, reverse=True), 1):
        st = pl.article_settings(a)
        if not st["enabled"] or k > 2:
            continue
        if st["use_article_image"] and a.image_url:
            shot = design.prepare(media.download_image(a.image_url), False)
        else:
            cat = st["photo_category"]
            p = next(x for x in products if x.category == cat and x.lot_size == 5)
            shot, _ = best_shot(p, False)
        paths.append(save(design.blog_post(st["short_title"], shot), f"2_blog_{k}"))
        print(f"blog {k}: {st['short_title']}")

    for k, (msg, variant) in enumerate([(services[0], "a-gravure-blanc"), (services[1], "a-gravure-brume")], 1):
        paths.append(save(design.service_post(msg["title"], msg["body"], variant), f"3_gravure_{k}_{variant}"))

    p, _ = picks[0]
    shot, _ = best_shot(p, False)
    paths.append(save(design.product_story(p, shot, "a-blanc"), "4_story_produit"))
    paths.append(save(design.service_story(services[3]["title"], services[3]["body"], "a-gravure-blanc"),
                      "4_story_gravure"))
    sheet([x for x in paths if "story" not in x.name], OUT / "planche_posts.jpg", cols=4)
    sheet([x for x in paths if "story" in x.name], OUT / "planche_stories.jpg", cols=2)


def sheet(paths, out, cols, h=900):
    ims = [Image.open(p) for p in paths]
    ims = [im.resize((round(im.width * h / im.height), h), Image.LANCZOS) for im in ims]
    cw, gap = max(i.width for i in ims), 36
    rows = (len(ims) + cols - 1) // cols
    s = Image.new("RGB", (cols * cw + (cols + 1) * gap, rows * h + (rows + 1) * gap), (226, 227, 230))
    for i, im in enumerate(ims):
        s.paste(im, (gap + (i % cols) * (cw + gap), gap + (i // cols) * (h + gap)))
    s.save(out, quality=90)


if __name__ == "__main__":
    main()
