"""Manual catch-up of ONE failed Instagram story (publish-test --recover-failed)."""
import json
from datetime import datetime, timedelta

import pytest

from comptoir import run
from comptoir.history import Entry, History

CONFIRM = run.CONFIRM_PHRASE
UID = "2026-11-17-tue-service-story"


def _setup(tmp_path, monkeypatch, status="failed", platforms=None, days_ago=1, publish=None):
    d = (datetime.now(run.TZ) - timedelta(days=days_ago)).date().isoformat()
    q = {"uid": UID, "date": d, "time": "18:30", "slot": "tue-service-story", "family": "service",
         "surface": "story", "facebook": True, "status": status, "reason": "Meta 400 code=200 sub=5258005",
         "subject": "gravure-identifier", "reference": "gravure", "category": "service", "layout": "a-gravure-blanc",
         "photo_url": "", "photo_source": "typographic", "price": "", "lot_size": 0, "title": "Gravure", "body": "",
         "url": "", "payload": {"image": "site/media/s.jpg", "facebook_image": "site/media/s-fb.jpg",
                                "caption_instagram": "", "caption_facebook": "FB"}}
    other = dict(q, uid="2026-11-18-wed-blog", status="planned", surface="post", family="blog", date=d)
    run.save_queue([q, other])
    p = tmp_path / "history.json"
    History([Entry(uid=UID, date=d, slot="tue-service-story", family="service", surface="story",
                   subject="gravure-identifier", reference="gravure", layout="a-gravure-blanc",
                   status="failed", platforms=platforms or {}, error="Meta 400")], p).save()
    monkeypatch.setattr(run.History, "load", classmethod(
        lambda cls, path=None: History([Entry(**e) for e in json.loads(p.read_text())], p)))
    monkeypatch.setattr(run.catalog, "load_catalog", lambda **k: [])
    monkeypatch.setattr(run.checks, "check_item", lambda it, products, verify_urls=True: [] if it.status == "planned" else ["statut"])
    calls = []
    monkeypatch.setattr(run, "_client", lambda: object())

    def fake_publish(q, e, client, only=None):
        calls.append(only)
        (publish or (lambda q, e: e.platforms.update(
            instagram={"status": "published", "id": "STORY1", "container": "C1"})))(q, e)
        # mimic _publish_one: IG only → story with facebook_image stays partial
        if e.platforms.get("instagram", {}).get("status") == "published":
            q["status"] = e.status = "partial"
    monkeypatch.setattr(run, "_publish_one", fake_publish)
    return calls, p


def args(*extra):
    return ["publish-test", "--confirm", CONFIRM, "--uid", UID, "--surface", "story", *extra]


def test_recovers_failed_story_on_instagram_once(tmp_path, monkeypatch):
    calls, p = _setup(tmp_path, monkeypatch)
    run.main(args("--platform", "instagram", "--recover-failed"))
    assert calls == ["instagram"]
    q = next(x for x in run.load_queue() if x["uid"] == UID)
    e = json.loads(p.read_text())[0]
    assert q["status"] == "published" and e["status"] == "published"
    assert e["platforms"]["instagram"]["id"] == "STORY1" and e["platforms"]["facebook"]["status"] == "skipped"
    other = next(x for x in run.load_queue() if x["uid"] != UID)
    assert other["status"] == "planned"                       # calendar untouched
    with pytest.raises(SystemExit):                           # second attempt refused: no longer failed
        run.main(args("--platform", "instagram", "--recover-failed"))
    assert calls == ["instagram"]


def test_failed_item_refused_without_recover_flag(tmp_path, monkeypatch):
    calls, _ = _setup(tmp_path, monkeypatch)
    with pytest.raises(SystemExit, match="Aucun élément"):
        run.main(args("--platform", "instagram"))
    assert calls == []


@pytest.mark.parametrize("extra", [["--platform", "both"], ["--platform", "facebook"], ["--platform", ""]])
def test_recover_requires_instagram_only(tmp_path, monkeypatch, extra):
    calls, _ = _setup(tmp_path, monkeypatch)
    with pytest.raises(SystemExit, match="instagram"):
        run.main(args(*extra, "--recover-failed"))
    assert calls == []


def test_recover_requires_uid(tmp_path, monkeypatch):
    calls, _ = _setup(tmp_path, monkeypatch)
    with pytest.raises(SystemExit):
        run.main(["publish-test", "--confirm", CONFIRM, "--platform", "instagram", "--recover-failed"])
    assert calls == []


@pytest.mark.parametrize("platforms", [{"instagram": {"status": "published", "id": "X"}},
                                       {"instagram": {"status": "uncertain"}}])
def test_recover_refused_if_already_published_or_uncertain(tmp_path, monkeypatch, platforms):
    calls, _ = _setup(tmp_path, monkeypatch, platforms=platforms)
    with pytest.raises(SystemExit, match="déjà publié|incertain"):
        run.main(args("--platform", "instagram", "--recover-failed"))
    assert calls == []


def test_recover_refused_after_7_days(tmp_path, monkeypatch):
    calls, _ = _setup(tmp_path, monkeypatch, days_ago=9)
    with pytest.raises(SystemExit, match="7 jours"):
        run.main(args("--platform", "instagram", "--recover-failed"))
    assert calls == []


def test_meta_refusal_keeps_failed_without_retry(tmp_path, monkeypatch):
    def refuse(q, e):
        q["status"] = e.status = "failed"
    calls, p = _setup(tmp_path, monkeypatch, publish=refuse)
    run.main(args("--platform", "instagram", "--recover-failed"))
    assert calls == ["instagram"]
    assert next(x for x in run.load_queue() if x["uid"] == UID)["status"] == "failed"


def test_uncertain_result_is_recorded_and_not_retried(tmp_path, monkeypatch):
    def unsure(q, e):
        e.platforms["instagram"] = {"status": "uncertain", "container": "C1"}
        q["status"] = e.status = "uncertain"
    calls, p = _setup(tmp_path, monkeypatch, publish=unsure)
    run.main(args("--platform", "instagram", "--recover-failed"))
    assert next(x for x in run.load_queue() if x["uid"] == UID)["status"] == "uncertain"
    with pytest.raises(SystemExit):
        run.main(args("--platform", "instagram", "--recover-failed"))
    assert calls == ["instagram"]


def test_paris_date_used_whatever_the_machine_timezone(monkeypatch):
    import time, os
    for tz in ("UTC", "Europe/Bucharest", "Pacific/Kiritimati", "America/Los_Angeles"):
        monkeypatch.setenv("TZ", tz)
        time.tzset()
        assert run.paris_today() == datetime.now(run.TZ).date()
    monkeypatch.delenv("TZ")
    time.tzset()
