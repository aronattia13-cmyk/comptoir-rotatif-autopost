"""Go-live safety: no catch-up, DST, failure signalling, WhatsApp wording."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from comptoir import captions, planner, run
from comptoir.history import Entry, History

TZ = ZoneInfo("Europe/Paris")


def test_dst_paris_times():
    winter = planner.PlanItem(uid="a", date="2026-11-16", time="12:30", slot="s", family="product",
                              surface="post", facebook=True)
    summer = planner.PlanItem(uid="b", date="2027-06-14", time="12:30", slot="s", family="product",
                              surface="post", facebook=True)
    assert winter.scheduled_at().utcoffset() == timedelta(hours=1)
    assert summer.scheduled_at().utcoffset() == timedelta(hours=2)
    assert winter.scheduled_at().astimezone(ZoneInfo("UTC")).hour == 11
    assert summer.scheduled_at().astimezone(ZoneInfo("UTC")).hour == 10
    # Couverture réelle de due.yml (7 j/7, été/hiver) : voir tests/test_rythme_7j.py


def _setup(tmp_path, monkeypatch, scheduled: datetime, activated: datetime | None):
    flag = tmp_path / "LIVE_ENABLED"
    flag.write_text(f"activated_at: {activated.isoformat()}\n" if activated else "on\n")
    monkeypatch.setattr(run, "LIVE_FLAG", flag)
    q = {"uid": "u1", "date": scheduled.date().isoformat(), "time": scheduled.strftime("%H:%M"), "slot": "mon-product",
         "family": "product", "surface": "post", "facebook": True, "status": "planned", "reason": "",
         "subject": "s", "reference": "r", "category": "turbine", "layout": "a-blanc", "photo_url": "x",
         "photo_source": "product", "price": "1.00", "lot_size": 5, "title": "T", "body": "", "url": "u",
         "payload": {"image": "site/media/x.jpg", "caption_instagram": "IG", "caption_facebook": "FB"}}
    run.save_queue([q])
    h = History([Entry(uid="u1", date=q["date"], slot="mon-product", family="product", surface="post",
                       subject="s", reference="r", layout="a-blanc")], tmp_path / "history.json")
    h.save()
    monkeypatch.setattr(run.History, "load", classmethod(lambda cls, path=None: History.__new__(cls)))
    monkeypatch.setattr(run.History, "load", classmethod(lambda cls, path=None: _load(tmp_path)))
    monkeypatch.setattr(run.catalog, "load_catalog", lambda **k: [])
    published = []
    monkeypatch.setattr(run, "_client", lambda: object())
    monkeypatch.setattr(run, "_publish_one", lambda q, e, client, only=None: published.append(q["uid"]) or
                        q.update(status="published"))
    monkeypatch.setattr(run.checks, "check_item", lambda it, products, verify_urls=True: [])
    return published


def _load(tmp_path):
    import json
    from comptoir.history import Entry as E
    p = tmp_path / "history.json"
    return History([E(**e) for e in json.loads(p.read_text())], p)


def test_past_slot_is_not_caught_up_after_activation(tmp_path, monkeypatch):
    now = datetime.now(TZ)
    pub = _setup(tmp_path, monkeypatch, scheduled=now - timedelta(minutes=30), activated=now - timedelta(minutes=5))
    run.main(["due", "--mode", "auto"])
    assert pub == []
    assert run.load_queue()[0]["status"] == "missed"


def test_very_late_slot_is_missed(tmp_path, monkeypatch):
    now = datetime.now(TZ)
    pub = _setup(tmp_path, monkeypatch, scheduled=now - timedelta(hours=3), activated=now - timedelta(days=2))
    with pytest.raises(SystemExit):
        run.main(["due", "--mode", "auto"])
    assert pub == [] and run.load_queue()[0]["status"] == "missed"


def test_missed_slot_makes_run_red_once_never_silent_green(tmp_path, monkeypatch):
    """Régression 20/11/2026 : GitHub n'a lancé aucune exécution entre 9 h et 14 h ; le créneau
    de 12 h 30 aurait été marqué « manqué » dans une exécution VERTE. Il doit la rendre rouge
    (e-mail au propriétaire), une seule fois, sans jamais publier en retard."""
    now = datetime.now(TZ)
    pub = _setup(tmp_path, monkeypatch, scheduled=now - timedelta(minutes=91), activated=now - timedelta(days=4))
    with pytest.raises(SystemExit) as exc:
        run.main(["due", "--mode", "auto"])
    assert "1 publication" in str(exc.value)
    assert pub == [] and run.load_queue()[0]["status"] == "missed"
    run.main(["due", "--mode", "auto"])                     # next run: already reported, green, nothing sent
    assert pub == []


def test_slot_before_activation_stays_green(tmp_path, monkeypatch):
    now = datetime.now(TZ)
    pub = _setup(tmp_path, monkeypatch, scheduled=now - timedelta(hours=3), activated=now - timedelta(minutes=5))
    run.main(["due", "--mode", "auto"])                     # expected: no exit, no publication
    assert pub == [] and run.load_queue()[0]["status"] == "missed"


def test_on_time_slot_is_published_once(tmp_path, monkeypatch):
    now = datetime.now(TZ)
    pub = _setup(tmp_path, monkeypatch, scheduled=now - timedelta(minutes=10), activated=now - timedelta(days=1))
    run.main(["due", "--mode", "auto"])
    run.main(["due", "--mode", "auto"])                     # second run: nothing more
    assert pub == ["u1"]


def test_future_slot_untouched(tmp_path, monkeypatch):
    now = datetime.now(TZ)
    pub = _setup(tmp_path, monkeypatch, scheduled=now + timedelta(hours=2), activated=now)
    run.main(["due", "--mode", "auto"])
    assert pub == [] and run.load_queue()[0]["status"] == "planned"


def test_failure_makes_the_run_red_without_retry(tmp_path, monkeypatch):
    now = datetime.now(TZ)
    _setup(tmp_path, monkeypatch, scheduled=now - timedelta(minutes=10), activated=now - timedelta(days=1))
    calls = []
    monkeypatch.setattr(run, "_publish_one", lambda q, e, client, only=None: calls.append(1) or
                        q.update(status="failed"))
    with pytest.raises(SystemExit):
        run.main(["due", "--mode", "auto"])
    run.main(["due", "--mode", "auto"])                     # failed item is not retried
    assert calls == [1]


def test_service_wording_without_whatsapp():
    ig = captions.service_caption({"title": "T", "body": "B"}, "instagram", None)
    assert "WhatsApp" not in ig and "message privé" in ig
    assert "WhatsApp" in captions.service_caption({"title": "T", "body": "B"}, "instagram", "https://wa.me/33600000000")
