"""Command-line entry point used by GitHub Actions.

  python -m comptoir.run weekly          refresh catalogue + blog, plan next 7 days,
                                         render images, update dashboard + bio page
  python -m comptoir.run due [--mode test]   check & (simulate) publications due now

  python -m comptoir.run meta-check [--container-test]   Meta diagnostics, publishes nothing
  python -m comptoir.run publish-test --confirm "<phrase>"  ONE real test publication

MODE: 'test' (default) never calls Meta. Automatic live publishing stays
locked until config/LIVE_ENABLED exists (phase 4).
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import blog, build, catalog, checks, planner, whatsapp
from .history import History

ROOT = Path(__file__).resolve().parents[2]
QUEUE = ROOT / "data" / "queue.json"
LOG = ROOT / "data" / "log.jsonl"
SITE = ROOT / "site"
TZ = ZoneInfo("Europe/Paris")
LIVE_FLAG = ROOT / "config" / "LIVE_ENABLED"


def log(event: str, **kw):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": datetime.now(TZ).isoformat(timespec="seconds"), "event": event, **kw}
    with LOG.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(json.dumps(rec, ensure_ascii=False))


def load_queue() -> list[dict]:
    return json.loads(QUEUE.read_text()) if QUEUE.exists() else []


def save_queue(q: list[dict]):
    QUEUE.write_text(json.dumps(q, ensure_ascii=False, indent=1))


# --------------------------------------------------------------------- #
def cmd_weekly(args):
    products = catalog.load_catalog(storefront_token=os.environ.get("SHOPIFY_STOREFRONT_TOKEN") or None)
    art_file = ROOT / "data" / "articles.json"
    try:
        articles = blog.fetch_articles()
    except Exception as exc:                     # rate limit / outage: reuse last known list
        articles = blog.from_dicts(json.loads(art_file.read_text())) if art_file.exists() else []
        log("blog indisponible, dernière liste réutilisée", error=str(exc)[:200], articles=len(articles))
    (ROOT / "data" / "catalog.json").write_text(json.dumps(
        {"fetched": datetime.now(TZ).isoformat(timespec="seconds"), "products": [p.to_dict() for p in products]},
        ensure_ascii=False, indent=1))
    art_file.write_text(json.dumps([a.to_dict() for a in articles], ensure_ascii=False, indent=1))
    log("catalogue", products=len(products), publishable=len(catalog.publishable(products)),
        articles=len(blog.usable(articles)))

    history = History.load()
    start = date.today() + timedelta(days=1)
    items = planner.Planner(products, articles, history).plan(start, args.days)
    queue = [q for q in load_queue() if q["status"] in ("planned",) and q["date"] >= date.today().isoformat()]
    for it in items:
        try:
            build.build_item(it, products, articles)
        except Exception as exc:                       # never publish a half-built item
            it.status, it.reason = "skipped", f"rendu impossible: {exc}"
            e = history.get(it.uid)
            if e:
                e.status, e.error = "skipped", it.reason
        queue.append(it.to_dict())
        log("planifié" if it.status == "planned" else "créneau vide", uid=it.uid, family=it.family,
            title=it.title, reason=it.reason)
    history.save()
    save_queue(queue)
    write_dashboard(products, articles, history, queue)
    write_bio_page(articles, products)


def cmd_due(args):
    """Publications whose time has come. TEST mode: checks + simulated status."""
    mode = args.mode
    if mode == "live" and not LIVE_FLAG.exists():
        sys.exit("Publication réelle verrouillée : config/LIVE_ENABLED absent (validation phase 4 requise).")
    products = catalog.load_catalog(storefront_token=os.environ.get("SHOPIFY_STOREFRONT_TOKEN") or None)
    history = History.load()
    queue = load_queue()
    now = datetime.now(TZ)
    client = None
    secrets = [os.environ.get(k, "") for k in ("META_SYSTEM_USER_TOKEN", "META_APP_SECRET", "SHOPIFY_STOREFRONT_TOKEN")]
    for q in queue:
        it = planner.PlanItem(**{**q, "status": "planned" if q["status"] == "partial" else q["status"]})
        if q["status"] not in ("planned", "partial") or it.scheduled_at() > now or history.is_published(it.uid):
            continue
        errors = checks.check_item(it, products)
        text = (it.payload.get("caption_instagram", "") + it.payload.get("caption_facebook", ""))
        if checks.scan_for_secrets(text, secrets):
            errors.append("secret détecté dans une légende")
        e = history.get(it.uid)
        if errors:
            q["status"] = "blocked"
            q["reason"] = "; ".join(errors)
            if e:
                e.status, e.error = "failed", q["reason"]
            log("bloqué", uid=it.uid, errors=errors)
            continue
        if mode == "test":
            q["status"] = "simulated"
            if e:
                e.status = "simulated"
            log("simulé (mode TEST, rien publié)", uid=it.uid, title=it.title)
        else:
            client = client or _client()
            _publish_one(q, e, client=client)
    history.save()
    save_queue(queue)


def _settings() -> dict:
    return json.loads((ROOT / "config" / "settings.json").read_text())


def _client():
    from .meta import MetaClient, MetaConfig
    return MetaClient(MetaConfig.from_env(_settings()["meta"]["graph_version"]))


def _base_url() -> str:
    url = os.environ.get("PUBLIC_BASE_URL", "").strip()
    if not url.startswith("https://"):
        sys.exit("Variable PUBLIC_BASE_URL absente (ex. https://<compte>.github.io/<dépôt>)")
    return url


def _publish_one(q: dict, e, client) -> None:
    """Real publication of one queue item, with per-platform idempotency."""
    from .meta import MetaError, redact
    from .publisher import MediaNotPublic, publish_item
    cfg = _settings()["meta"]
    done = {k for k, v in (e.platforms if e else {}).items() if v.get("status") == "published"}
    try:
        res = publish_item(q, client, _base_url(), done,
                           poll=cfg["container_poll_seconds"], timeout=cfg["container_timeout_seconds"])
    except (MetaError, MediaNotPublic) as exc:
        msg = redact(str(exc))
        q["status"], q["reason"] = "failed", msg
        if e:
            e.status, e.error = "failed", msg
        log("échec publication", uid=q["uid"], error=msg)
        return
    if e:
        e.platforms.update(res)
    needed = {"instagram"} | ({"facebook"} if q.get("facebook") and
                              (q["surface"] != "story" or q["payload"].get("facebook_image")) else set())
    states = {k: v.get("status") for k, v in (e.platforms if e else res).items()}
    if any(v == "uncertain" for v in states.values()):
        q["status"] = "uncertain"
        if e:
            e.status = "uncertain"
        log("RÉSULTAT INCERTAIN — vérifier manuellement, pas de nouvel essai", uid=q["uid"], results=res)
    elif needed <= {k for k, v in states.items() if v == "published"}:
        q["status"] = "published"
        if e:
            e.status = "published"
        log("publié", uid=q["uid"], links={k: v.get("permalink") for k, v in res.items()})
    else:
        q["status"] = "partial"
        log("publication partielle", uid=q["uid"], results=res)


def cmd_meta_check(args):
    """Connection diagnostics. Publishes NOTHING."""
    from .meta import redact
    from .publisher import diagnose
    client = _client()
    sample = None
    media = SITE / "media"
    if media.exists():
        jpgs = sorted(p for p in media.glob("*.jpg") if not p.name.endswith("-fb.jpg"))
        sample = f"site/media/{jpgs[0].name}" if jpgs else None
    base = os.environ.get("PUBLIC_BASE_URL", "").strip() or None
    rows = diagnose(client, base, sample, container_test=args.container_test)
    ok = all(r[1] for r in rows)
    for name, good, detail in rows:
        print(f"{'OK ' if good else 'ÉCHEC'}  {name}: {redact(detail)}")
    print("\nRappel : un diagnostic réussi ne prouve pas qu'une publication réelle fonctionnera ;"
          " seule la publication de test (après accord) le prouve.")
    (ROOT / "data" / "meta_check.json").write_text(json.dumps(
        {"ts": datetime.now(TZ).isoformat(timespec="seconds"), "ok": ok,
         "checks": [{"name": n, "ok": g, "detail": redact(d)} for n, g, d in rows]}, ensure_ascii=False, indent=1))
    if not ok:
        sys.exit(1)


CONFIRM_PHRASE = "JE CONFIRME LA PUBLICATION DE TEST"


def cmd_publish_test(args):
    """ONE real publication, only with the exact confirmation phrase."""
    if args.confirm != CONFIRM_PHRASE:
        sys.exit(f"Refus : phrase de confirmation attendue « {CONFIRM_PHRASE} ».")
    history = History.load()
    queue = load_queue()
    cands = [q for q in queue if q["status"] == "planned" and (args.uid in ("", q["uid"]))]
    if args.surface:
        cands = [q for q in cands if q["surface"] == args.surface]
    if not cands:
        sys.exit("Aucun élément planifié correspondant dans la file.")
    q = cands[0]
    products = catalog.load_catalog()
    errors = checks.check_item(planner.PlanItem(**q), products)
    if errors:
        sys.exit("Contrôles bloquants : " + "; ".join(errors))
    e = history.get(q["uid"])
    log("PUBLICATION DE TEST demandée", uid=q["uid"], title=q["title"])
    _publish_one(q, e, client=_client())
    history.save()
    save_queue(queue)


# --------------------------------------------------------------------- #
def write_dashboard(products, articles, history: History, queue):
    upcoming = [q for q in queue if q["status"] == "planned"]
    done = sorted([e for e in history.entries if e.status in ("published", "simulated")], key=lambda e: e.date)
    errors = [e for e in history.entries if e.status in ("failed", "skipped") and e.error][-15:]
    esc = html.escape

    def card(q):
        img = q["payload"].get("image", "").replace("site/", "")
        return (f'<div class="c"><b>{q["date"]} {q["time"]}</b><br><small>{q["family"]} · {q["surface"]}'
                f' · {q["layout"]}</small><img src="{img}"><div>{esc(q["title"])}</div></div>')

    prices = "".join(f"<tr><td>{esc(p.display_name)}</td><td>{p.lot_size}</td><td>{catalog.format_price(p.price)}</td>"
                     f"<td>{'oui' if p.available else 'non'}</td><td>{esc('; '.join(p.warnings))}</td></tr>"
                     for p in products)
    def links(e):
        return " ".join(f'<a href="{esc(v.get("permalink", ""))}">{esc(k)}</a>' for k, v in e.platforms.items())

    hist = "".join(f"<tr><td>{e.date}</td><td>{e.family}</td><td>{esc(e.reference.split('/')[-1])}</td>"
                   f"<td>{e.status}</td><td>{links(e)}</td></tr>" for e in reversed(done[-30:]))
    errs = "".join(f"<li>{e.date} {e.slot}: {esc(e.error)}</li>" for e in errors) or "<li>Aucune</li>"
    last = done[-1] if done else None
    page = f'''<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Tableau de contrôle — Le Comptoir du Rotatif</title><meta name="robots" content="noindex">
<style>body{{font:14px/1.45 system-ui,sans-serif;margin:0;background:#f6f6f4;color:#141e2c}}main{{max-width:1300px;margin:auto;padding:24px}}
h1{{font-weight:500}}h2{{font-weight:500;margin-top:32px}}.g{{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px}}
.c{{background:#fff;border:1px solid #e0e3e8;border-radius:8px;padding:10px}}.c img{{width:100%;margin:6px 0;border:1px solid #eee}}
table{{border-collapse:collapse;width:100%;background:#fff}}td,th{{border-bottom:1px solid #eee;padding:6px 8px;text-align:left;font-size:13px}}</style></head>
<body><main><h1>Tableau de contrôle</h1>
<p>Mode : <b>{"RÉEL" if LIVE_FLAG.exists() else "TEST (aucune publication)"}</b> · mis à jour {datetime.now(TZ):%d/%m/%Y %H:%M}</p>
<p>Dernière publication : {esc(last.reference.split("/")[-1]) + " (" + last.date + ", " + last.status + ")" if last else "aucune"}</p>
<h2>Prochaines publications</h2><div class="g">{"".join(card(q) for q in upcoming) or "Aucune"}</div>
<h2>Erreurs et créneaux vides</h2><ul>{errs}</ul>
<h2>Historique</h2><table><tr><th>Date</th><th>Type</th><th>Sujet</th><th>Statut</th><th>Liens</th></tr>{hist}</table>
<h2>Prix récupérés dans Shopify</h2><table><tr><th>Produit</th><th>Lot</th><th>Prix</th><th>Dispo</th><th>Alertes</th></tr>{prices}</table>
</main></body></html>'''
    SITE.mkdir(exist_ok=True)
    (SITE / "index.html").write_text(page)


def write_bio_page(articles, products):
    """Link-in-bio page (real, clickable links): WhatsApp, articles, shop."""
    esc = html.escape
    wa = whatsapp.link_or_none("gravure")
    links = []
    if wa:
        links.append(("Gravure personnalisée — renseignements WhatsApp", wa))
    links.append(("Boutique en ligne", "https://lecomptoirdurotatif.com"))
    for a in sorted(blog.usable(articles), key=lambda a: a.published, reverse=True)[:5]:
        links.append((a.title, a.url))
    links.append(("Nous contacter", "https://lecomptoirdurotatif.com/pages/contact"))
    items = "".join(f'<a class="l" href="{esc(u)}">{esc(t)}</a>' for t, u in links)
    page = f'''<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Le Comptoir du Rotatif — liens</title><style>body{{margin:0;font:16px/1.4 system-ui,sans-serif;background:#f9f8f5;color:#141e2c}}
main{{max-width:480px;margin:auto;padding:40px 20px;text-align:center}}img{{height:70px}}.l{{display:block;margin:12px 0;padding:14px 16px;border:1px solid #141e2c;
border-radius:6px;color:#141e2c;text-decoration:none;background:#fff}}</style></head><body><main>
<img src="../logo.png" alt="Le Comptoir du Rotatif"><p>Instruments rotatifs dentaires</p>{items}</main></body></html>'''
    (SITE / "liens").mkdir(parents=True, exist_ok=True)
    (SITE / "liens" / "index.html").write_text(page)
    logo = ROOT / "assets" / "logo.png"
    if logo.exists():
        (SITE / "logo.png").write_bytes(logo.read_bytes())


def main(argv=None):
    ap = argparse.ArgumentParser(prog="comptoir")
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("weekly")
    w.add_argument("--days", type=int, default=7)
    d = sub.add_parser("due")
    d.add_argument("--mode", choices=["test", "live"], default=os.environ.get("PUBLISH_MODE", "test"))
    c = sub.add_parser("meta-check")
    c.add_argument("--container-test", action="store_true")
    t = sub.add_parser("publish-test")
    t.add_argument("--confirm", default="")
    t.add_argument("--uid", default="")
    t.add_argument("--surface", choices=["", "post", "story"], default="")
    args = ap.parse_args(argv)
    {"weekly": cmd_weekly, "due": cmd_due, "meta-check": cmd_meta_check,
     "publish-test": cmd_publish_test}[args.cmd](args)


if __name__ == "__main__":
    main()
