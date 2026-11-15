"""Direction A graphic system."""
import json
import re
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, features

from comptoir import catalog, design

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def products():
    return catalog.normalize_products_json(json.loads((FIX / "products_snapshot.json").read_text()))


def white_photo(w=900, h=1400):
    """Test-only stand-in for a studio shot on white."""
    im = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((w * .4, h * .1, w * .6, h * .9), 40, fill=(168, 170, 172))
    return im


@pytest.mark.parametrize("name,title,spec", [
    ("Contre-angle bague bleue 1:1 40 000 tr/min LED simple spray",
     "Contre-angle bague bleue 1:1", "40 000 tr/min · LED · simple spray"),
    ("Turbines raccord rapide KaVo – LED triple spray", "Turbines raccord rapide KaVo", "LED · triple spray"),
    ("Détartreur compatible Satelec DTE avec LED", "Détartreur compatible Satelec DTE", "avec LED"),
    ("Détartreurs compatibles EMS Woodpecker sans LED", "Détartreurs compatibles EMS Woodpecker", "sans LED"),
    ("Contre-angle 1:5 (bague rouge) LED spray", "Contre-angle 1:5 (bague rouge)", "LED · spray"),
    ("Raccord rapide type Multiflex KaVo", "Raccord rapide type Multiflex KaVo", ""),
])
def test_split_name(name, title, spec):
    assert design.split_name(name) == (title, spec)


def test_split_name_keeps_every_word_in_order(products):
    for p in products:
        t, s = design.split_name(p.display_name)
        words = lambda x: re.sub(r"[·–]", " ", x).split()
        assert words(t + " " + s) == words(p.display_name)


def test_wrap_avoids_orphans():
    f = design.serif(124)
    lines = design.wrap("Gravure personnalisée pour votre cabinet", f, 888, 3)
    assert lines[-1] != "cabinet" and "pour" not in lines[1].split()[-1:]


@pytest.mark.parametrize("variant", design.PRODUCT_VARIANTS)
def test_product_post_and_story(products, variant):
    longest = max(products, key=lambda p: len(p.display_name))
    shot = design.prepare(white_photo(), need_cutout=variant == "a-brume")
    post = design.product_post(longest, shot, variant)
    story = design.product_story(longest, shot, variant)
    assert post.size == (1080, 1350) and story.size == (1080, 1920)
    # margins stay clean: nothing drawn in the outer 40 px band
    a = np.asarray(post.convert("L"))
    bg = a[0, 0]
    assert (a[:40] == bg).all() and (a[-40:] == bg).all()
    assert (a[:, :40] == bg).all() and (a[:, -40:] == bg).all()


def test_story_respects_instagram_safe_zone(products):
    shot = design.prepare(white_photo(), False)
    a = np.asarray(design.product_story(products[0], shot).convert("L"))
    assert (a[:200] == 255).all()          # top UI zone empty
    assert (a[1640:] == 255).all()         # bottom UI zone empty


def test_never_upscaled_beyond_limit():
    small = design.prepare(white_photo(200, 300), False)
    canvas = Image.new("RGB", (1080, 1350), "white")
    x0, y0, x1, y1 = design.paste_shot(canvas, small, (0, 0, 1080, 1350))
    assert (y1 - y0) <= small.native[1] * design.MAX_UPSCALE + 1


def test_photo_not_altered_on_white():
    src = white_photo()
    shot = design.prepare(src, False)
    assert shot.image.mode == "RGB"
    assert shot.image.getpixel((shot.image.width // 2, shot.image.height // 2)) == (168, 170, 172)


def test_cutout_keeps_product_pixels():
    shot = design.prepare(white_photo(), True)
    assert shot.image.mode == "RGBA"
    c = shot.image.getpixel((shot.image.width // 2, shot.image.height // 2))
    assert c == (168, 170, 172, 255)
    assert shot.image.getpixel((0, 0))[3] < 40


def test_choose_photo_prefers_sharper_same_framing():
    small = white_photo(450, 700)
    big = white_photo(1800, 2800)
    close_up = Image.new("RGB", (1600, 1600), (200, 200, 200))
    assert design.choose_photo([small, close_up, big]) == 2
    assert design.choose_photo([small, close_up]) == 0


def test_logo_never_enlarged():
    raw = Image.open(design.LOGO)
    assert design.logo(5000).width <= raw.width


def test_blog_and_service_render():
    shot = design.prepare(white_photo(), False)
    assert design.blog_post("Un titre court d'article", shot).size == (1080, 1350)
    assert design.blog_story("Un titre court d'article", shot).size == (1080, 1920)
    for v in design.SERVICE_VARIANTS:
        assert design.service_post("Gravure personnalisée", "Une phrase.", v).size == (1080, 1350)
        assert design.service_story("Gravure personnalisée", "Une phrase.", v).size == (1080, 1920)


def test_kerning_engine_available():
    assert features.check("raqm"), "libraqm missing: kerning would be off"
