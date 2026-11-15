import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import unquote

import pytest

from comptoir import blog, captions, catalog, checks, planner, whatsapp
from comptoir.history import Entry, History

FIX = Path(__file__).parent / "fixtures"
START = date(2026, 11, 16)  # a Monday


@pytest.fixture(scope="module")
def products():
    return catalog.normalize_products_json(json.loads((FIX / "products_snapshot.json").read_text()))


@pytest.fixture(scope="module")
def articles():
    arts = blog.parse_atom((FIX / "blog_infos.atom").read_text(), "infos")
    imgs = blog.parse_article_images((FIX / "sitemap_articles.xml").read_text())
    for a in arts:
        a.image_url = imgs.get(a.url)
        a.url_ok = True
    return arts


def plan(products, articles, history=None, weeks=4, **kw):
    return planner.simulate(products, articles, history or History([]), START, weeks, **kw)


# ---------------------------------------------------------------- blog ----
def test_blog_parses_real_articles(articles):
    assert len(articles) == 3
    for a in articles:
        assert a.url.startswith("https://lecomptoirdurotatif.com/blogs/infos/")
        assert a.title and a.summary and a.image_url


def test_blog_summary_is_verbatim_excerpt(articles):
    a = next(a for a in articles if "organisation" in a.handle)
    assert a.summary.startswith("En France, les contraintes réglementaires s’intensifient")
    assert len(a.summary) <= 221


def test_unreachable_article_is_not_usable(articles):
    import copy
    arts = copy.deepcopy(articles)
    arts[0].url_ok = False
    assert arts[0] not in blog.usable(arts)


# ------------------------------------------------------------ whatsapp ----
@pytest.mark.parametrize("raw", ["+33 6 12 34 56 78", "0033612345678", "33612345678", "+33-6.12.34.56.78"])
def test_whatsapp_number_normalised(raw):
    assert whatsapp.normalize_number(raw) == "33612345678"


@pytest.mark.parametrize("raw", ["", None, "0612345678", "+33abc", "123"])
def test_whatsapp_bad_numbers_rejected(raw):
    with pytest.raises(whatsapp.WhatsAppConfigError):
        whatsapp.normalize_number(raw)


def test_whatsapp_link_prefilled():
    link = whatsapp.build_link("+33612345678")
    assert link.startswith("https://wa.me/33612345678?text=")
    assert unquote(link.split("text=")[1]) == whatsapp.DEFAULT_MESSAGES["gravure"]


def test_whatsapp_never_invented(monkeypatch):
    monkeypatch.delenv("WHATSAPP_NUMBER", raising=False)
    assert whatsapp.link_or_none() is None
    cap = captions.service_caption({"title": "T", "body": "B"}, "facebook", None)
    assert "wa.me" not in cap and "/pages/contact" in cap


# ------------------------------------------------------------- planner ----
def test_weekly_rhythm(products, articles):
    items = plan(products, articles, weeks=1)
    assert [(date.fromisoformat(i.date).weekday(), i.family, i.surface) for i in items] == [
        (0, "product", "post"), (1, "service", "story"), (2, "blog", "post"),
        (3, "product", "story"), (4, "product", "post")]


def test_friday_is_another_range(products, articles):
    items = plan(products, articles)
    by_week = {}
    for i in items:
        by_week.setdefault(date.fromisoformat(i.date).isocalendar()[1], {})[i.slot] = i
    for wk in by_week.values():
        mon, fri = wk.get("mon-product"), wk.get("fri-product-range")
        if mon and fri and mon.status == fri.status == "planned":
            assert mon.category != fri.category


def test_no_product_repeated_within_cooldown(products, articles):
    items = [i for i in plan(products, articles, weeks=6) if i.status == "planned" and i.family == "product"]
    seen = {}
    for i in items:
        d = date.fromisoformat(i.date)
        if i.reference in seen:
            assert (d - seen[i.reference]).days >= 42
        seen[i.reference] = d


def test_same_model_spaced(products, articles):
    items = [i for i in plan(products, articles, weeks=6) if i.status == "planned" and i.family == "product"]
    last = {}
    for i in items:
        d = date.fromisoformat(i.date)
        if i.subject in last:
            assert (d - last[i.subject]).days >= 21
        last[i.subject] = d


def test_articles_never_repeated_and_disabled_excluded(articles, products):
    items = [i for i in plan(products, articles, weeks=8) if i.family == "blog" and i.status == "planned"]
    urls = [i.url for i in items]
    assert len(urls) == len(set(urls))
    assert not any("cabinets-dentaires-fermes" in u for u in urls)


def test_skips_instead_of_duplicating(products, articles):
    items = plan(products, articles, weeks=4)
    blog_slots = [i for i in items if i.family == "blog"]
    assert sum(i.status == "planned" for i in blog_slots) == 2       # only 2 enabled articles
    assert all(i.reason for i in blog_slots if i.status == "skipped")


def test_rare_variants_never_back_to_back(products, articles):
    items = [i for i in plan(products, articles, weeks=8) if i.status == "planned" and i.family == "product"]
    for a, b in zip(items, items[1:]):
        assert not (a.layout == b.layout and a.layout != "a-blanc")


def test_history_prevents_repeat(products, articles):
    h = History([])
    first = planner.Planner(products, articles, h).plan(START, 7)
    mon = first[0]
    assert h.has(mon.uid)
    again = planner.Planner(products, articles, h).plan(START, 7)
    assert again == []                                   # nothing re-planned
    later = planner.Planner(products, articles, h).plan(START + timedelta(days=7), 7)
    assert mon.reference not in [i.reference for i in later]


def test_history_refuses_duplicate_uid():
    h = History([])
    e = Entry(uid="2026-11-16-mon-product", date="2026-11-16", slot="mon-product", family="product",
              surface="post", subject="x", reference="y", layout="zoom")
    h.add(e)
    with pytest.raises(ValueError):
        h.add(e)


def test_planner_does_not_mutate_real_history(products, articles):
    h = History([])
    plan(products, articles, h)
    assert h.entries == []


def test_service_is_typographic_without_real_photo_and_rotates(products, articles):
    items = [i for i in plan(products, articles, service_photos=[]) if i.family == "service"]
    planned = [i for i in items if i.status == "planned"]
    assert planned and all(i.photo_source == "typographic" and not i.photo_url for i in planned)
    assert len({i.subject for i in planned}) == len(planned)


def test_unavailable_never_planned(products, articles):
    import copy
    prods = copy.deepcopy(products)
    for p in prods:
        if p.category == "turbine":
            p.available = False
    items = plan(prods, articles)
    assert all(i.category != "turbine" for i in items if i.family == "product")


# -------------------------------------------------------------- checks ----
def test_price_change_blocks_publication(products, articles):
    import copy
    item = next(i for i in plan(products, articles, weeks=1) if i.family == "product")
    prods = copy.deepcopy(products)
    p = next(x for x in prods if x.handle == item.reference)
    assert checks.check_item(item, prods, verify_urls=False) == []
    p.price = p.price + Decimal("10")
    assert any("prix modifié" in e for e in checks.check_item(item, prods, verify_urls=False))
    p.price -= Decimal("10")
    p.available = False
    assert any("indisponible" in e for e in checks.check_item(item, prods, verify_urls=False))


def test_secret_scan():
    assert checks.scan_for_secrets("token EAAB123456 here", ["EAAB123456"])
    assert not checks.scan_for_secrets("clean caption", ["EAAB123456", ""])


# ------------------------------------------------------------ captions ----
def test_product_caption_uses_exact_price(products):
    p = next(x for x in products if x.lot_size == 50)
    ig = captions.product_caption(p, "seed", "instagram")
    fb = captions.product_caption(p, "seed", "facebook")
    price = catalog.format_price(p.price).replace(" ", " ")
    assert price in ig and price in fb
    assert "lot de 50" in ig.lower()
    assert p.url in fb and "lien en bio" in ig
    for word in ("promo", "remise", "-%", "soldes", "offert"):
        assert word not in ig.lower()


def test_captions_deterministic(products):
    p = products[0]
    assert captions.product_caption(p, "a", "instagram") == captions.product_caption(p, "a", "instagram")


def test_blog_caption_links(articles):
    a = articles[0]
    assert a.url in captions.blog_caption(a, a.title, "s", "facebook", True)
    assert "lien en bio" in captions.blog_caption(a, a.title, "s", "instagram", True)


def test_articles_roundtrip(articles):
    assert [a.url for a in blog.from_dicts([a.to_dict() for a in articles])] == [a.url for a in articles]


def test_http_retries_on_429(monkeypatch):
    from comptoir import net

    class R:
        def __init__(self, code):
            self.status_code, self.headers = code, {"Retry-After": "1"}

        def raise_for_status(self):
            pass

    calls = iter([R(429), R(429), R(200)])
    monkeypatch.setattr(net.requests, "get", lambda *a, **k: next(calls))
    waits = []
    assert net.get("https://x", sleep=waits.append).status_code == 200
    assert waits == [1.0, 1.0]


def test_variant_mix_70_20_10(products, articles):
    import collections
    from comptoir.planner import Planner
    h = History([])
    pl = Planner(products, articles, h)
    picks = []
    for k in range(60):
        v = pl._pick_weighted(START + timedelta(days=k), pl.s["product_variants"])
        picks.append(v)
        h.entries.append(Entry(uid=f"x{k}", date=(START + timedelta(days=k)).isoformat(), slot="s",
                               family="product", surface="post", subject=f"s{k}", reference=f"r{k}",
                               layout=v, status="simulated"))
    c = collections.Counter(picks[-40:])
    assert c["a-blanc"] == 28 and c["a-brume"] == 8 and c["a-blanc-serif"] == 4
    assert all(not (a == b == "a-blanc-serif") for a, b in zip(picks, picks[1:]))
