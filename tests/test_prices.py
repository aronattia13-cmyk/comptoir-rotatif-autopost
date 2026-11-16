"""Shopify price changes: future visuals regenerated, outdated images never sent."""
import copy
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from comptoir import catalog, checks, planner, publisher, run
from comptoir.history import Entry, History
from test_meta import client as meta_client, ok_get

FIX = Path(__file__).parent / "fixtures"
TZ = ZoneInfo("Europe/Paris")
HANDLE = "10-contre-angle-bague-bleue-1-1-40-000-tours-mn-led-simple-spray"


@pytest.fixture
def products():
    return catalog.normalize_products_json(json.loads((FIX / "products_snapshot.json").read_text()))


def item(price="4529.99", when=None, status="planned"):
    when = when or datetime.now(TZ) + timedelta(days=1)
    return {"uid": f"{when.date()}-mon-product", "date": when.date().isoformat(), "time": when.strftime("%H:%M"),
            "slot": "mon-product", "family": "product", "surface": "post", "facebook": True, "status": status,
            "reason": "", "subject": "s", "reference": HANDLE, "category": "contre-angle", "layout": "a-blanc",
            "photo_url": "x", "photo_source": "product", "price": price, "lot_size": 50,
            "title": "Contre-angle bague bleue 1:1 40 000 tr/min LED simple spray", "body": "", "url": "u",
            "payload": {"image": "site/media/x.jpg", "image_sha256": f"sha-{price}", "rendered_price": price,
                        "caption_instagram": f"IG {price}", "caption_facebook": f"FB {price}"}}


def env(tmp_path, monkeypatch, products, queue, platforms=None):
    run.save_queue(queue)
    hist = History([Entry(uid=q["uid"], date=q["date"], slot=q["slot"], family="product", surface="post",
                          subject="s", reference=q["reference"], layout="a-blanc",
                          platforms=copy.deepcopy(platforms or {})) for q in queue], tmp_path / "h.json")
    monkeypatch.setattr(run.History, "load", classmethod(lambda cls, path=None: hist))
    monkeypatch.setattr(run.catalog, "load_catalog", lambda **k: products)
    rebuilt = []

    def fake_build(it, prods, arts, *a, **k):          # no network / rendering in tests
        rebuilt.append((it.uid, it.price))
        it.payload.update(image_sha256=f"sha-{it.price}", rendered_price=it.price,
                          caption_instagram=f"IG {it.price}", caption_facebook=f"FB {it.price}")
        return it
    monkeypatch.setattr(run.build, "build_item", fake_build)
    monkeypatch.setattr(run, "write_dashboard", lambda *a, **k: None)
    monkeypatch.setattr(run, "write_bio_page", lambda *a, **k: None)
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    return hist, rebuilt, out


def set_price(products, value):
    p = next(x for x in products if x.handle == HANDLE)
    p.price = Decimal(value)
    return p


# ------------------------------------------------------------------ refresh ----
def test_price_change_regenerates_visual_and_captions(tmp_path, monkeypatch, products):
    set_price(products, "4299.00")
    hist, rebuilt, out = env(tmp_path, monkeypatch, products, [item("4529.99")])
    run.main(["refresh"])
    q = run.load_queue()[0]
    assert rebuilt == [(q["uid"], "4299.00")]
    assert q["price"] == "4299.00" and q["payload"]["rendered_price"] == "4299.00"
    assert "4299.00" in q["payload"]["caption_instagram"] and q["payload"]["image_sha256"] == "sha-4299.00"
    assert q["status"] == "planned"
    assert "site_changed=true" in out.read_text()
    assert "visuel régénéré" in run.LOG.read_text()


def test_unchanged_price_does_nothing(tmp_path, monkeypatch, products):
    hist, rebuilt, out = env(tmp_path, monkeypatch, products, [item("4529.99")])
    run.main(["refresh"])
    assert rebuilt == [] and "site_changed=false" in out.read_text()


def test_visual_showing_old_price_is_regenerated_even_if_slot_price_is_right(tmp_path, monkeypatch, products):
    q = item("4529.99")
    q["payload"]["rendered_price"] = "4999.00"
    hist, rebuilt, out = env(tmp_path, monkeypatch, products, [q])
    run.main(["refresh"])
    assert rebuilt and run.load_queue()[0]["payload"]["rendered_price"] == "4529.99"


def test_published_and_past_slots_untouched(tmp_path, monkeypatch, products):
    set_price(products, "4299.00")
    past = item("4529.99", when=datetime.now(TZ) - timedelta(days=2))
    done = item("4529.99", status="published")
    hist, rebuilt, out = env(tmp_path, monkeypatch, products, [past, done])
    run.main(["refresh"])
    assert rebuilt == []


def test_unavailable_product_cancels_slot(tmp_path, monkeypatch, products):
    next(x for x in products if x.handle == HANDLE).available = False
    hist, rebuilt, out = env(tmp_path, monkeypatch, products, [item()])
    run.main(["refresh"])
    assert run.load_queue()[0]["status"] == "skipped" and rebuilt == []


def test_partial_slot_regenerated_for_remaining_network(tmp_path, monkeypatch, products):
    set_price(products, "4299.00")
    hist, rebuilt, out = env(tmp_path, monkeypatch, products, [item(status="partial")],
                             platforms={"instagram": {"status": "published", "id": "M1"}})
    run.main(["refresh"])
    assert rebuilt and run.load_queue()[0]["price"] == "4299.00"


# ------------------------------------------------------------------- checks ----
def test_check_blocks_visual_with_old_price(products):
    it = planner.PlanItem(**{**item("4529.99"), "status": "planned"})
    it.payload["rendered_price"] = "4999.00"
    errs = checks.check_item(it, products, verify_urls=False)
    assert any("ancien prix" in e for e in errs) and checks.needs_regeneration(errs)


def test_check_blocks_when_shopify_price_changed(products):
    set_price(products, "4299.00")
    errs = checks.check_item(planner.PlanItem(**item("4529.99")), products, verify_urls=False)
    assert checks.needs_regeneration(errs)


def test_unavailable_is_not_a_regeneration_case(products):
    next(x for x in products if x.handle == HANDLE).available = False
    errs = checks.check_item(planner.PlanItem(**item("4529.99")), products, verify_urls=False)
    assert errs and not checks.needs_regeneration(errs)


# ----------------------------------------------------------- publication ----
def test_due_never_sends_old_price_then_publishes_new_one(tmp_path, monkeypatch, products):
    run.LIVE_FLAG.write_text(f"activated_at: {(datetime.now(TZ) - timedelta(days=1)).isoformat()}\n")
    q = item("4529.99", when=datetime.now(TZ) - timedelta(minutes=5))
    hist, rebuilt, out = env(tmp_path, monkeypatch, products, [q])
    monkeypatch.setattr(run.checks, "check_url", lambda u: True, raising=False)
    monkeypatch.setattr("comptoir.checks.check_url", lambda u: True)
    sent = []
    monkeypatch.setattr(run, "_client", lambda: "FAKE")
    monkeypatch.setattr(run, "_publish_one", lambda q, e, client, only=None:
                        sent.append(q["payload"]["rendered_price"]) or q.update(status="published"))
    set_price(products, "4299.00")                    # price changes just before the slot
    run.main(["due", "--mode", "auto"])               # must not send, must not fail the run
    assert sent == [] and run.load_queue()[0]["status"] == "planned"
    run.main(["refresh"])                             # next pass: regenerated (then Pages redeploy)
    run.main(["due", "--mode", "auto"])
    assert sent == ["4299.00"]


def test_stale_public_image_is_never_sent():
    with pytest.raises(publisher.StaleMedia):
        publisher.verify_public_image("u", (1080, 1350), get=ok_get(), expected_sha256="old-version")
    c = meta_client()
    q = item()
    q["payload"]["image_sha256"] = "new-version-not-yet-online"
    with pytest.raises(publisher.StaleMedia):
        publisher.publish_item(q, c, "https://u.github.io/r", set(), get=ok_get())
    assert not any(x[0] == "POST" for x in c.s.calls)          # nothing sent to Meta


def test_stale_image_keeps_slot_for_retry(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://u.github.io/r")
    monkeypatch.setattr("comptoir.publisher.net.get", lambda u, **k: ok_get()(u))
    q = item()
    q["payload"]["image_sha256"] = "not-online-yet"
    e = Entry(uid=q["uid"], date=q["date"], slot="mon-product", family="product", surface="post",
              subject="s", reference=HANDLE, layout="a-blanc")
    run._publish_one(q, e, meta_client())
    assert q["status"] == "planned" and "pas à jour" in q["reason"] and not e.platforms


def test_facebook_stale_after_instagram_keeps_instagram_result(monkeypatch):
    import hashlib
    good = hashlib.sha256(ok_get()("u").content).hexdigest()
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://u.github.io/r")
    monkeypatch.setattr("comptoir.publisher.net.get", lambda u, **k: ok_get()(u))
    q = item()
    q["payload"].update(image_sha256=good, facebook_image="site/media/x-fb.jpg", facebook_image_sha256="stale")
    e = Entry(uid=q["uid"], date=q["date"], slot="mon-product", family="product", surface="post",
              subject="s", reference=HANDLE, layout="a-blanc")
    run._publish_one(q, e, meta_client())
    assert e.platforms["instagram"]["status"] == "published"
    assert e.platforms["facebook"]["status"] == "retry" and q["status"] == "partial"
    c2 = meta_client()
    q["payload"]["facebook_image_sha256"] = good
    run._publish_one(q, e, c2)
    assert not any(x[1].endswith("media_publish") for x in c2.s.calls)   # IG never twice
    assert e.platforms["facebook"]["status"] == "published" and q["status"] == "published"
