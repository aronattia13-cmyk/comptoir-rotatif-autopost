"""Rythme 7 j/7 : story Instagram 09:30, post Instagram + Facebook 16:30 (Europe/Paris),
~5 publicités produits par semaine, contrôles par canal, rattrapage sans doublon."""
import json
import re
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from comptoir import blog, catalog, planner, run
from comptoir.history import Entry, History

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).parent / "fixtures"
SETTINGS = json.loads((ROOT / "config" / "settings.json").read_text())
TZ = ZoneInfo("Europe/Paris")
START = date(2026, 11, 23)  # lundi


@pytest.fixture(scope="module")
def products():
    return catalog.normalize_products_json(json.loads((FIX / "products_snapshot.json").read_text()))


@pytest.fixture(scope="module")
def articles():
    arts = blog.parse_atom((FIX / "blog_infos.atom").read_text(), "infos")
    for a in arts:
        a.url_ok = True
    return arts


def tips(validated=True):
    return [dict(t, validated=validated) for t in json.loads((ROOT / "data/content/tips.json").read_text())["tips"]]


def plan(products, articles, weeks=4, history=None, **kw):
    kw.setdefault("tips", tips())
    return planner.simulate(products, articles, history or History([]), START, weeks, settings=SETTINGS, **kw)


def fam(products, item):
    p = next(x for x in products if x.handle == item.reference)
    return planner.product_family(p, SETTINGS["product_families"])


def test_quatorze_creneaux_par_semaine_week_end_compris(products, articles):
    items = plan(products, articles, weeks=1)
    assert len(items) == 14
    for d in range(7):
        day = [i for i in items if date.fromisoformat(i.date).weekday() == d]
        story, post = sorted(day, key=lambda i: i.time)
        assert (story.time, story.surface, story.facebook) == ("09:30", "story", False)
        assert (post.time, post.surface, post.facebook) == ("16:30", "post", True)


def test_cinq_publicites_produits_par_semaine_toutes_familles(products, articles):
    items = plan(products, articles, weeks=4)
    assert all(i.status == "planned" for i in items)
    for w in range(4):
        week = [i for i in items if i.family == "product" and (date.fromisoformat(i.date) - START).days // 7 == w]
        assert len(week) == 5
        assert len({fam(products, i) for i in week}) == 5          # turbines, Satelec, EMS, CA bleu, CA rouge


def test_jamais_la_meme_famille_deux_jours_de_suite(products, articles):
    prods = [i for i in plan(products, articles, weeks=6) if i.family == "product"]
    for a, b in zip(prods, prods[1:]):
        if (date.fromisoformat(b.date) - date.fromisoformat(a.date)).days <= 1:
            assert fam(products, a) != fam(products, b), (a.uid, b.uid)


def test_jamais_deux_fois_le_meme_produit_lot_support_visuel(products, articles):
    prods = [i for i in plan(products, articles, weeks=6) if i.family == "product"]
    keys = Counter((i.reference, i.surface, i.layout) for i in prods)
    assert max(keys.values()) == 1


def test_historique_compte_pour_les_visuels(products, articles):
    first = [i for i in plan(products, articles, weeks=1) if i.family == "product"][0]
    h = History([Entry(uid="x", date="2026-11-01", slot="old", family="product", surface=first.surface,
                       subject="s", reference=first.reference, layout=first.layout, photo="", category=first.category,
                       status="published")])
    again = [i for i in plan(products, articles, weeks=6, history=h) if i.family == "product"]
    assert (first.reference, first.surface, first.layout) not in {(i.reference, i.surface, i.layout) for i in again}


def test_prix_et_lot_viennent_de_shopify(products, articles):
    for i in plan(products, articles, weeks=2):
        if i.family == "product":
            p = next(x for x in products if x.handle == i.reference)
            assert (i.price, i.lot_size) == (str(p.price), p.lot_size)


def test_conseils_non_valides_jamais_publies(products, articles):
    items = plan(products, articles, weeks=1, tips=tips(validated=False))
    assert not any(i.family == "tip" and i.status == "planned" for i in items)
    assert any(i.status == "skipped" for i in items)           # signalé (planification rouge), jamais comblé par une répétition


def test_ancien_calendrier_retire_sans_toucher_au_publie(tmp_path):
    q = [{"uid": "2026-11-23-mon-product", "slot": "mon-product", "date": "2026-11-23", "status": "planned"},
         {"uid": "2026-11-19-thu-product-story", "slot": "thu-product-story", "date": "2026-11-19", "status": "published"}]
    run.save_queue(q)
    h = History([Entry(uid="2026-11-23-mon-product", date="2026-11-23", slot="mon-product", family="product",
                       surface="post", subject="", reference="r", layout="a-blanc", photo="", category="turbine",
                       status="planned")])
    gone = run.drop_obsolete_slots(h, date(2026, 11, 23))
    assert gone == ["2026-11-23-mon-product"]
    assert [x["uid"] for x in run.load_queue()] == ["2026-11-19-thu-product-story"] and h.entries == []


def _cron_runs(expr):
    minute, hour, *_ = expr.split()
    hours = [h for part in hour.split(",") for h in (range(int(part.split("-")[0]), int(part.split("-")[1]) + 1)
                                                     if "-" in part else [int(part)])]
    return [(h, int(m)) for h in hours for m in minute.split(",")]


@pytest.mark.parametrize("utc_offset", [1, 2])   # hiver (UTC+1) et été (UTC+2)
@pytest.mark.parametrize("slot", ["09:30", "16:30"])
def test_planification_github_couvre_les_creneaux_7j(utc_offset, slot):
    wf = (ROOT / ".github/workflows/due.yml").read_text()
    expr = re.search(r'cron: "([^"]+)"', wf).group(1)
    assert expr.split()[4] == "*"                                 # tous les jours, week-end compris
    h, m = map(int, slot.split(":"))
    slot_utc = (h - utc_offset) * 60 + m
    assert any(0 <= hh * 60 + mm - slot_utc < 30 for hh, mm in _cron_runs(expr))


# ------------------------------------------------- contrôles par canal ----
def _queue_item(uid, surface, time, facebook):
    return {"uid": uid, "date": "2026-11-23", "time": time, "slot": uid[11:], "family": "product", "surface": surface,
            "facebook": facebook, "status": "published", "payload": {}}


def test_controle_separe_story_ig_post_ig_post_fb(monkeypatch):
    run.save_queue([_queue_item("2026-11-23-mon-story", "story", "09:30", False),
                    _queue_item("2026-11-23-mon-post", "post", "16:30", True)])
    h = History([Entry(uid="2026-11-23-mon-story", date="2026-11-23", slot="mon-story", family="tip", surface="story",
                       subject="", reference="", layout="", photo="", category="", status="published",
                       platforms={"instagram": {"status": "published", "id": "111"}}),
                 Entry(uid="2026-11-23-mon-post", date="2026-11-23", slot="mon-post", family="product", surface="post",
                       subject="", reference="", layout="", photo="", category="", status="partial",
                       platforms={"instagram": {"status": "published", "id": "222", "permalink": "https://instagram.com/p/x"},
                                  "facebook": {"status": "failed", "error": "refus Meta"}})])
    h.save()
    rows = run.channel_report(now=datetime(2026, 11, 23, 18, 0, tzinfo=TZ))
    assert [(r["canal"], r["ok"], r["id"]) for r in rows] == [
        ("Story Instagram", True, "111"), ("Post Instagram", True, "222"), ("Post Facebook", False, "")]
    with pytest.raises(SystemExit):
        monkeypatch.setattr(run, "channel_report", lambda **k: rows)
        run.cmd_check(SimpleNamespace(grace=45))


def test_controle_attend_le_delai_de_grace():
    run.save_queue([_queue_item("2026-11-23-mon-post", "post", "16:30", True)])
    History([]).save()
    assert run.channel_report(now=datetime(2026, 11, 23, 17, 0, tzinfo=TZ)) == []
    assert len(run.channel_report(now=datetime(2026, 11, 23, 17, 20, tzinfo=TZ))) == 2


def test_un_identifiant_sans_statut_publie_nest_pas_une_preuve():
    run.save_queue([_queue_item("2026-11-23-mon-story", "story", "09:30", False)])
    History([Entry(uid="2026-11-23-mon-story", date="2026-11-23", slot="mon-story", family="tip", surface="story",
                   subject="", reference="", layout="", photo="", category="", status="uncertain",
                   platforms={"instagram": {"status": "uncertain", "id": "999"}})]).save()
    assert run.channel_report(now=datetime(2026, 11, 23, 12, 0, tzinfo=TZ))[0]["ok"] is False


# ------------------------------------------------------------ rattrapage ----
def _catchup(**kw):
    a = dict(uid="2026-11-23-mon-post", platform="instagram", confirm=run.CATCHUP_PHRASE)
    a.update(kw)
    return SimpleNamespace(**a)


def test_rattrapage_exige_la_phrase():
    with pytest.raises(SystemExit, match="phrase"):
        run.cmd_catchup(_catchup(confirm="oui"))


def test_rattrapage_refuse_si_deja_publie_sur_ce_canal(monkeypatch):
    monkeypatch.setattr(run, "paris_today", lambda: date(2026, 11, 23))
    q = _queue_item("2026-11-23-mon-post", "post", "16:30", True) | {"status": "partial"}
    run.save_queue([q])
    History([Entry(uid=q["uid"], date=q["date"], slot="mon-post", family="product", surface="post", subject="",
                   reference="", layout="", photo="", category="", status="partial",
                   platforms={"instagram": {"status": "published", "id": "222"}})]).save()
    monkeypatch.setattr(run, "_client", lambda: pytest.fail("Meta ne doit pas être appelé"))
    with pytest.raises(SystemExit, match="déjà published"):
        run.cmd_catchup(_catchup())


def test_rattrapage_story_jamais_sur_facebook(monkeypatch):
    monkeypatch.setattr(run, "paris_today", lambda: date(2026, 11, 23))
    run.save_queue([_queue_item("2026-11-23-mon-story", "story", "09:30", False) | {"status": "missed"}])
    with pytest.raises(SystemExit, match="Facebook"):
        run.cmd_catchup(_catchup(uid="2026-11-23-mon-story", platform="facebook"))


def test_rattrapage_refuse_au_dela_de_deux_jours(monkeypatch):
    monkeypatch.setattr(run, "paris_today", lambda: date(2026, 11, 27))
    run.save_queue([_queue_item("2026-11-23-mon-post", "post", "16:30", True) | {"status": "missed"}])
    with pytest.raises(SystemExit, match="2 jours"):
        run.cmd_catchup(_catchup())


def test_rattrapage_refuse_si_publication_identique_visible_chez_meta(monkeypatch):
    monkeypatch.setattr(run, "paris_today", lambda: date(2026, 11, 23))
    q = _queue_item("2026-11-23-mon-post", "post", "16:30", True) | {"status": "missed",
        "payload": {"caption_instagram": "Turbines raccord rapide KaVo\n\nLot de 5"}}
    run.save_queue([q])
    History([]).save()
    fake = SimpleNamespace(ig_recent_media=lambda n: [{"caption": "Turbines raccord rapide KaVo\n\nLot de 5"}])
    monkeypatch.setattr(run, "_client", lambda: fake)
    with pytest.raises(SystemExit, match="identique"):
        run.cmd_catchup(_catchup())


def test_conseil_rendu_typographique_et_legende(tmp_path, products, articles, monkeypatch):
    from comptoir import build
    monkeypatch.setattr(build, "ROOT", tmp_path)
    t = tips()[0]
    for surface in ("story", "post"):
        it = planner.PlanItem(uid=f"2026-11-23-x-{surface}", date="2026-11-23", time="09:30", slot="x", family="tip",
                              surface=surface, facebook=surface == "post", subject=t["id"], reference="conseils",
                              category="tip", layout=planner.SERVICE_LAYOUTS[0], title=t["title"], body=t["body"],
                              photo_source="typographic")
        build.build_item(it, products, articles, out_dir=tmp_path)
        assert (tmp_path / f"{it.uid}.jpg").exists()
        assert it.payload["rendered_price"] == ""
        if surface == "post":
            assert t["title"] in it.payload["caption_instagram"] and "#conseildentaire" in it.payload["caption_instagram"]
            assert it.payload["caption_facebook"].startswith(t["title"])
