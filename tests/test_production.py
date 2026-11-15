"""Production flow: per-platform tests, blog fallback, auto mode, bio page."""
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from comptoir import blog, catalog, planner, run
from comptoir.history import Entry, History
from test_meta import BASE, Resp, client as meta_client, ok_get

FIX = Path(__file__).parent / "fixtures"
START = date(2026, 11, 16)


@pytest.fixture(scope="module")
def products():
    return catalog.normalize_products_json(json.loads((FIX / "products_snapshot.json").read_text()))


@pytest.fixture(scope="module")
def articles():
    arts = blog.parse_atom((FIX / "blog_infos.atom").read_text(), "infos")
    for a in arts:
        a.url_ok = True
    return arts


def test_blog_slot_replaced_by_other_range_product_when_articles_exhausted(products, articles):
    items = planner.simulate(products, articles, History([]), START, 4)
    wed = [i for i in items if i.slot == "wed-blog"]
    assert sum(i.family == "blog" and i.status == "planned" for i in wed) == 2   # 2 enabled real articles
    repl = [i for i in wed if i.family == "product"]
    assert repl and all("remplacement" in i.reason and i.price for i in repl)
    refs = [i.reference for i in items if i.status == "planned" and i.family == "product"]
    assert len(refs) == len(set(refs))                               # still no product repeated


def queue_item():
    return {"uid": "2026-11-16-mon-product", "date": "2026-11-16", "time": "12:30", "surface": "post",
            "family": "product", "facebook": True, "status": "planned", "title": "T", "url": "u",
            "payload": {"image": "site/media/x.jpg", "caption_instagram": "IG", "caption_facebook": "FB"}}


def entry():
    return Entry(uid="2026-11-16-mon-product", date="2026-11-16", slot="mon-product", family="product",
                 surface="post", subject="s", reference="r", layout="a-blanc")


def test_platform_by_platform_tests(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://u.github.io/r")
    monkeypatch.setattr("comptoir.publisher.net.get", lambda u, **k: ok_get()(u))
    q, e = queue_item(), entry()
    c = meta_client()
    run._publish_one(q, e, c, only="instagram")
    assert e.platforms["instagram"]["status"] == "published" and "facebook" not in e.platforms
    assert q["status"] == "partial" and e.status == "partial"
    assert not any(x[1].endswith("photos") for x in c.s.calls)
    c2 = meta_client()
    run._publish_one(q, e, c2, only="facebook")
    assert e.platforms["facebook"]["status"] == "published"
    assert q["status"] == "published" and e.status == "published"
    assert not any(x[1].endswith("media_publish") for x in c2.s.calls)     # IG never twice
    c3 = meta_client()
    run._publish_one(q, e, c3, only="instagram")                            # repeat: no-op
    assert not c3.s.calls


def test_due_auto_mode_is_test_without_flag(tmp_path, monkeypatch):
    assert not run.LIVE_FLAG.exists()
    monkeypatch.setattr(run, "QUEUE", tmp_path / "q.json")
    monkeypatch.setattr(run, "LOG", tmp_path / "log.jsonl")
    monkeypatch.setattr("comptoir.history.HISTORY_FILE", tmp_path / "h.json")
    monkeypatch.setattr(run.History, "load", classmethod(lambda cls, path=None: History([], tmp_path / "h.json")))
    monkeypatch.setattr(run.catalog, "load_catalog", lambda **k: [])
    run.QUEUE.write_text("[]")
    run.main(["due", "--mode", "auto"])
    assert '"mode": "test"' in run.LOG.read_text()


def test_bio_page_excludes_disabled_article_and_adds_whatsapp(tmp_path, monkeypatch, articles):
    monkeypatch.setattr(run, "SITE", tmp_path)
    monkeypatch.setenv("WHATSAPP_NUMBER", "+33 6 12 34 56 78")
    q = [{"uid": "x", "date": date.today().isoformat(), "status": "planned", "family": "product",
          "title": "Turbine", "lot_size": 5, "url": "https://lecomptoirdurotatif.com/products/turbine"}]
    run.write_bio_page(articles, [], q)
    page = (tmp_path / "liens" / "index.html").read_text()
    assert "https://wa.me/33612345678?text=" in page
    assert "cabinets-dentaires-fermes" not in page                     # disabled pending review
    assert "products/turbine" in page and "lot de 5" in page


def test_bio_page_without_whatsapp_has_no_fake_number(tmp_path, monkeypatch, articles):
    monkeypatch.setattr(run, "SITE", tmp_path)
    monkeypatch.delenv("WHATSAPP_NUMBER", raising=False)
    run.write_bio_page(articles, [], [])
    assert "wa.me" not in (tmp_path / "liens" / "index.html").read_text()
