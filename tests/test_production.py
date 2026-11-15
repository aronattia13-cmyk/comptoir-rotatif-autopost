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


def _due_env(tmp_path, monkeypatch, scheduled_minutes_ago=10):
    """One due item in an isolated queue; publisher stubbed (no Meta call)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    when = datetime.now(ZoneInfo("Europe/Paris")) - timedelta(minutes=scheduled_minutes_ago)
    q = queue_item()
    q.update(date=when.date().isoformat(), time=when.strftime("%H:%M"), slot="mon-product", reason="",
             subject="s", reference="r", category="turbine", layout="a-blanc", photo_url="x",
             photo_source="product", price="1.00", lot_size=5, body="")
    run.save_queue([q])
    hist = History([Entry(uid=q["uid"], date=q["date"], slot="mon-product", family="product", surface="post",
                          subject="s", reference="r", layout="a-blanc")], tmp_path / "h.json")
    monkeypatch.setattr(run.History, "load", classmethod(lambda cls, path=None: hist))
    monkeypatch.setattr(run.catalog, "load_catalog", lambda **k: [])
    monkeypatch.setattr(run.checks, "check_item", lambda it, products, verify_urls=True: [])
    calls = []
    monkeypatch.setattr(run, "_client", lambda: "FAKE-CLIENT")
    monkeypatch.setattr(run, "_publish_one", lambda q, e, client, only=None:
                        calls.append((q["uid"], client)) or q.update(status="published"))
    return calls


def test_due_auto_mode_is_test_without_flag(tmp_path, monkeypatch):
    assert not run.LIVE_FLAG.exists()                 # isolated fake path (conftest)
    calls = _due_env(tmp_path, monkeypatch)
    run.main(["due", "--mode", "auto"])
    assert '"mode": "test"' in run.LOG.read_text()
    assert calls == []                                # no real publication
    assert run.load_queue()[0]["status"] == "simulated"


def test_due_auto_mode_is_live_with_valid_flag(tmp_path, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    activated = datetime.now(ZoneInfo("Europe/Paris")) - timedelta(days=1)
    run.LIVE_FLAG.write_text(f"activated_at: {activated.isoformat()}\n")
    calls = _due_env(tmp_path, monkeypatch)
    run.main(["due", "--mode", "auto"])
    assert '"mode": "live"' in run.LOG.read_text()
    assert calls == [("2026-11-16-mon-product", "FAKE-CLIENT")]    # stubbed publisher only
    run.main(["due", "--mode", "auto"])
    assert len(calls) == 1                            # never twice


def test_live_flag_does_not_allow_catch_up(tmp_path, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Europe/Paris"))
    run.LIVE_FLAG.write_text(f"activated_at: {now.isoformat()}\n")
    calls = _due_env(tmp_path, monkeypatch, scheduled_minutes_ago=20)   # slot before activation
    run.main(["due", "--mode", "auto"])
    assert calls == [] and run.load_queue()[0]["status"] == "missed"


def test_activated_at_parsing(tmp_path):
    from datetime import datetime
    run.LIVE_FLAG.write_text("activated_at: 2026-11-16T00:30:00+01:00\n")
    assert run.live_activated_at() == datetime.fromisoformat("2026-11-16T00:30:00+01:00")
    run.LIVE_FLAG.write_text("# comment\nactivated_at: 2026-11-16T00:30:00+01:00\nother: x\n")
    assert run.live_activated_at().utcoffset() == timedelta(hours=1)
    run.LIVE_FLAG.write_text("on\n")                 # no date: only the 90-min window applies
    assert run.live_activated_at() is None


def test_real_live_flag_untouched_by_tests():
    real = Path(__file__).resolve().parents[1] / "config" / "LIVE_ENABLED"
    assert run.LIVE_FLAG != real


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
