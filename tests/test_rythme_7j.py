"""Rythme 7 j/7 : post Instagram + Facebook 09:30, Story Instagram 16:30 (Europe/Paris),
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
        post, story = sorted(day, key=lambda i: i.time)
        assert (post.time, post.surface, post.facebook) == ("09:30", "post", True)
        assert (story.time, story.surface, story.facebook) == ("16:30", "story", False)


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
    prods = [i for i in plan(products, articles, weeks=8) if i.family == "product"]   # 8 semaines < délai de retour
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
    run.save_queue([_queue_item("2026-11-23-mon-post", "post", "09:30", True),
                    _queue_item("2026-11-23-mon-story", "story", "16:30", False)])
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
        ("Post Instagram", True, "222"), ("Post Facebook", False, ""), ("Story Instagram", True, "111")]
    with pytest.raises(SystemExit):
        monkeypatch.setattr(run, "channel_report", lambda **k: rows)
        run.cmd_check(SimpleNamespace(grace=45))


def test_controle_attend_le_delai_de_grace():
    run.save_queue([_queue_item("2026-11-23-mon-post", "post", "09:30", True)])
    History([]).save()
    assert run.channel_report(now=datetime(2026, 11, 23, 10, 0, tzinfo=TZ)) == []
    assert len(run.channel_report(now=datetime(2026, 11, 23, 10, 20, tzinfo=TZ))) == 2


def test_un_identifiant_sans_statut_publie_nest_pas_une_preuve():
    run.save_queue([_queue_item("2026-11-23-mon-story", "story", "16:30", False)])
    History([Entry(uid="2026-11-23-mon-story", date="2026-11-23", slot="mon-story", family="tip", surface="story",
                   subject="", reference="", layout="", photo="", category="", status="uncertain",
                   platforms={"instagram": {"status": "uncertain", "id": "999"}})]).save()
    assert run.channel_report(now=datetime(2026, 11, 23, 18, 0, tzinfo=TZ))[0]["ok"] is False


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
    q = _queue_item("2026-11-23-mon-post", "post", "09:30", True) | {"status": "partial"}
    run.save_queue([q])
    History([Entry(uid=q["uid"], date=q["date"], slot="mon-post", family="product", surface="post", subject="",
                   reference="", layout="", photo="", category="", status="partial",
                   platforms={"instagram": {"status": "published", "id": "222"}})]).save()
    monkeypatch.setattr(run, "_client", lambda: pytest.fail("Meta ne doit pas être appelé"))
    with pytest.raises(SystemExit, match="déjà published"):
        run.cmd_catchup(_catchup())


def test_rattrapage_story_jamais_sur_facebook(monkeypatch):
    monkeypatch.setattr(run, "paris_today", lambda: date(2026, 11, 23))
    run.save_queue([_queue_item("2026-11-23-mon-story", "story", "16:30", False) | {"status": "missed"}])
    with pytest.raises(SystemExit, match="Facebook"):
        run.cmd_catchup(_catchup(uid="2026-11-23-mon-story", platform="facebook"))


def test_rattrapage_refuse_au_dela_de_deux_jours(monkeypatch):
    monkeypatch.setattr(run, "paris_today", lambda: date(2026, 11, 27))
    run.save_queue([_queue_item("2026-11-23-mon-post", "post", "09:30", True) | {"status": "missed"}])
    with pytest.raises(SystemExit, match="2 jours"):
        run.cmd_catchup(_catchup())


def test_rattrapage_refuse_si_publication_identique_visible_chez_meta(monkeypatch):
    monkeypatch.setattr(run, "paris_today", lambda: date(2026, 11, 23))
    q = _queue_item("2026-11-23-mon-post", "post", "09:30", True) | {"status": "missed",
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


# ------------------------------------------ samedis, dimanches, changements d'heure ----
from datetime import timezone

JOURS_CRITIQUES = [
    ("2026-11-28", "samedi"), ("2026-11-29", "dimanche"),
    ("2027-03-28", "dimanche passage à l'heure d'été"), ("2027-10-31", "dimanche retour à l'heure d'hiver"),
]


def _freeze(monkeypatch, moment):
    class FrozenDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)
    monkeypatch.setattr(run, "datetime", FrozenDT)


@pytest.mark.parametrize("jour,label", JOURS_CRITIQUES)
@pytest.mark.parametrize("heure,surface", [("09:30", "post"), ("16:30", "story")])
def test_programmation_github_couvre_le_creneau_ce_jour_la(jour, label, heure, surface):
    wf = (ROOT / ".github/workflows/due.yml").read_text()
    expr = re.search(r'cron: "([^"]+)"', wf).group(1)
    assert expr.split()[2:] == ["*", "*", "*"]                     # tous les jours du mois, tous les mois, tous les jours
    h, m = map(int, heure.split(":"))
    slot_utc = datetime.fromisoformat(jour).replace(hour=h, minute=m, tzinfo=TZ).astimezone(timezone.utc)
    mins = slot_utc.hour * 60 + slot_utc.minute
    assert any(0 <= hh * 60 + mm - mins < 30 for hh, mm in _cron_runs(expr)), (label, slot_utc)


@pytest.mark.parametrize("jour,label", JOURS_CRITIQUES)
def test_heure_de_paris_juste_les_jours_de_changement(jour, label):
    it = planner.PlanItem(uid="u", date=jour, time="09:30", slot="s", family="tip", surface="post", facebook=True)
    utc = it.scheduled_at().astimezone(timezone.utc)
    attendu = {"2026-11-28": 8, "2026-11-29": 8, "2027-03-28": 7, "2027-10-31": 8}[jour]   # 09:30 Paris en UTC
    assert (utc.hour, utc.minute) == (attendu, 30)


@pytest.mark.parametrize("jour,label", JOURS_CRITIQUES)
@pytest.mark.parametrize("heure,surface,facebook", [("09:30", "post", True), ("16:30", "story", False)])
def test_publication_envoyee_le_week_end_et_aux_changements_d_heure(tmp_path, monkeypatch, jour, label, heure, surface, facebook):
    from test_golive import _setup
    h, m = map(int, heure.split(":"))
    slot = datetime.fromisoformat(jour).replace(hour=h, minute=m, tzinfo=TZ)
    pub = _setup(tmp_path, monkeypatch, scheduled=slot, activated=slot - timedelta(days=3))
    q = run.load_queue()[0]
    q.update(surface=surface, facebook=facebook)
    run.save_queue([q])
    _freeze(monkeypatch, slot + timedelta(minutes=7))
    run.main(["due", "--mode", "auto"])
    assert pub == ["u1"], label


@pytest.mark.parametrize("jour,label", JOURS_CRITIQUES)
def test_creneau_manque_le_week_end_rend_l_execution_rouge(tmp_path, monkeypatch, jour, label):
    from test_golive import _setup
    slot = datetime.fromisoformat(jour).replace(hour=9, minute=30, tzinfo=TZ)
    pub = _setup(tmp_path, monkeypatch, scheduled=slot, activated=slot - timedelta(days=3))
    _freeze(monkeypatch, slot + timedelta(minutes=95))
    with pytest.raises(SystemExit):
        run.main(["due", "--mode", "auto"])
    assert pub == [] and run.load_queue()[0]["status"] == "missed"


def test_un_an_sans_epuisement_ni_doublon_rapproche(products, articles):
    """52 semaines : 5 familles différentes chaque semaine ; un visuel identique (produit + lot + support +
    variante) ne revient jamais avant cooldown_days.same_visual jours."""
    items = [i for i in plan(products, articles, weeks=52) if i.family == "product"]
    for w in range(52):
        week = [i for i in items if (date.fromisoformat(i.date) - START).days // 7 == w]
        assert len(week) == 5 and len({fam(products, i) for i in week}) == 5, w
    last, gap = {}, SETTINGS["cooldown_days"]["same_visual"]
    for i in items:
        k = (i.reference, i.surface, i.layout)
        if k in last:
            assert (date.fromisoformat(i.date) - last[k]).days >= gap
        last[k] = date.fromisoformat(i.date)


def test_chaque_famille_passe_en_post_et_en_story(products, articles):
    items = [i for i in plan(products, articles, weeks=10) if i.family == "product"]
    for f in SETTINGS["product_families"]:
        assert {i.surface for i in items if fam(products, i) == f} == {"post", "story"}, f
