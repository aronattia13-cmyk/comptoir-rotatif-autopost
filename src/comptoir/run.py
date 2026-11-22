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


def paris_today() -> date:
    """Calendar date in Paris, whatever the machine's time zone (GitHub runners use UTC)."""
    return datetime.now(TZ).date()
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
    start = paris_today() + timedelta(days=1)
    removed = drop_obsolete_slots(history, start)
    if removed:
        log("créneaux de l'ancien calendrier retirés (jamais publiés)", uids=removed)
    items = planner.Planner(products, articles, history).plan(start, args.days)
    queue = [q for q in load_queue() if q["status"] in ("planned",) and q["date"] >= paris_today().isoformat()
             and q["uid"] not in removed]
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
    write_bio_page(articles, products, queue)
    empty = [it.uid for it in items if it.status != "planned"]
    if empty:
        # Alerte dès la planification (exécution rouge + e-mail) : un créneau vide se corrige avant le jour J.
        sys.exit(f"{len(empty)} créneau(x) sans contenu cette semaine : {', '.join(empty)} (voir le tableau de contrôle)")


def drop_obsolete_slots(history: History, start: date) -> list[str]:
    """Retire de la file et de l'historique les créneaux FUTURS encore « planned » dont le nom
    n'existe plus dans config/settings.json (changement de calendrier). Rien de publié n'est touché."""
    valid = {s["slot"] for s in _settings()["weekly_slots"]}
    queue = load_queue()
    gone = [q["uid"] for q in queue if q["status"] == "planned" and q["date"] >= start.isoformat()
            and q["slot"] not in valid]
    gone += [e.uid for e in history.entries if e.status == "planned" and e.date >= start.isoformat()
             and e.slot not in valid and e.uid not in gone]
    if gone:
        save_queue([q for q in queue if q["uid"] not in gone])
        history.entries[:] = [e for e in history.entries if e.uid not in gone]
    return gone


def cmd_due(args):
    """Publications whose time has come. TEST mode: checks + simulated status."""
    mode = args.mode
    if mode == "auto":
        mode = "live" if LIVE_FLAG.exists() else "test"
    if mode == "live" and not LIVE_FLAG.exists():
        sys.exit("Publication réelle verrouillée : config/LIVE_ENABLED absent (validation phase 4 requise).")
    products = catalog.load_catalog(storefront_token=os.environ.get("SHOPIFY_STOREFRONT_TOKEN") or None)
    history = History.load()
    queue = load_queue()
    now = datetime.now(TZ)
    max_delay = timedelta(minutes=_settings()["meta"].get("max_delay_minutes", 90))
    activated = live_activated_at() if mode == "live" else None
    problems = 0
    client = None
    announced = False
    secrets = [os.environ.get(k, "") for k in ("META_SYSTEM_USER_TOKEN", "META_APP_SECRET", "SHOPIFY_STOREFRONT_TOKEN")]
    for q in queue:
        it = planner.PlanItem(**{**q, "status": "planned" if q["status"] == "partial" else q["status"]})
        if q["status"] not in ("planned", "partial") or it.scheduled_at() > now or history.is_published(it.uid):
            continue
        e = history.get(it.uid)
        if not announced:
            log("mode", mode=mode)
            announced = True
        late = now - it.scheduled_at()
        if mode == "live" and (late > max_delay or (activated and it.scheduled_at() < activated)):
            # No catch-up: a past slot is never published late.
            q["status"], q["reason"] = "missed", f"créneau passé ({int(late.total_seconds() // 60)} min) : non rattrapé"
            if e and e.status != "partial":
                e.status, e.error = "missed", q["reason"]
            log("créneau non rattrapé", uid=it.uid, retard_min=int(late.total_seconds() // 60))
            if late > max_delay and not (activated and it.scheduled_at() < activated):
                # An expected publication was NOT sent (e.g. GitHub did not start the scheduled
                # run in time): the run must be red so the owner is e-mailed — never a silent green.
                # (A slot that predates LIVE activation is expected and stays green.)
                problems += 1
            continue
        errors = checks.check_item(it, products)
        text = (it.payload.get("caption_instagram", "") + it.payload.get("caption_facebook", ""))
        if checks.scan_for_secrets(text, secrets):
            errors.append("secret détecté dans une légende")
        if errors and checks.needs_regeneration(errors):
            # Price / lot changed or visual outdated: never sent; the refresh
            # step regenerates and redeploys, the slot is retried next run
            # (within the 90-min window, otherwise "missed").
            q["reason"] = "en attente de régénération : " + "; ".join(errors)
            log("bloqué — visuel à régénérer, nouvel essai au prochain passage", uid=it.uid, errors=errors)
            continue
        if errors:
            problems += 1
            q["status"] = "blocked"
            q["reason"] = "; ".join(errors)
            if e:
                e.status, e.error = "failed", q["reason"]
            log("bloqué", uid=it.uid, errors=errors)
            continue
        if mode == "test":
            if e and any(v.get("status") == "published" for v in e.platforms.values()):
                continue                      # already partly published for real (test): leave as is
            q["status"] = "simulated"
            if e:
                e.status = "simulated"
            log("simulé (mode TEST, rien publié)", uid=it.uid, title=it.title)
        else:
            client = client or _client()
            _publish_one(q, e, client=client)
            if q["status"] in ("failed", "uncertain") or (
                    q["status"] == "partial" and any(v.get("status") == "failed" for v in (e.platforms if e else {}).values())):
                problems += 1
    history.save()
    save_queue(queue)
    if problems:
        # Non-zero exit = GitHub marks the run red and e-mails the owner.
        # Nothing is retried automatically (no duplicate risk).
        sys.exit(f"{problems} publication(s) bloquée(s) ou en échec : voir data/log.jsonl et le tableau de contrôle")


def live_activated_at():
    """Activation time written in config/LIVE_ENABLED (slots before it are never published)."""
    try:
        for line in LIVE_FLAG.read_text().splitlines():
            if line.startswith("activated_at:"):
                return datetime.fromisoformat(line.split(":", 1)[1].strip())
    except (OSError, ValueError):
        pass
    return None


def _settings() -> dict:
    return json.loads((ROOT / "config" / "settings.json").read_text())


def _client():
    from .meta import MetaClient, MetaConfig, MetaConfigError
    try:
        return MetaClient(MetaConfig.from_env(_settings()["meta"]["graph_version"]))
    except MetaConfigError as exc:
        sys.exit(f"[CONFIG     ] {exc}")


def _base_url() -> str:
    url = os.environ.get("PUBLIC_BASE_URL", "").strip()
    if not url.startswith("https://"):
        sys.exit("Variable PUBLIC_BASE_URL absente (ex. https://<compte>.github.io/<dépôt>)")
    return url


def _publish_one(q: dict, e, client, only: str | None = None) -> None:
    """Real publication of one queue item, with per-platform idempotency.
    only='instagram' or 'facebook' restricts this run to one network (tests)."""
    from .meta import MetaError, redact
    from .publisher import MediaNotPublic, StaleMedia, publish_item
    cfg = _settings()["meta"]
    done = {k for k, v in (e.platforms if e else {}).items() if v.get("status") == "published"}
    if only:
        done |= {"instagram", "facebook"} - {only}
        if only in {k for k, v in (e.platforms if e else {}).items() if v.get("status") == "published"}:
            log("déjà publié sur cette plateforme — rien à faire", uid=q["uid"], platform=only)
            return
    try:
        res = publish_item(q, client, _base_url(), done,
                           poll=cfg["container_poll_seconds"], timeout=cfg["container_timeout_seconds"])
    except StaleMedia as exc:
        # Old visual still online: nothing sent, retried after redeploy.
        q["reason"] = redact(str(exc))
        log("image en ligne pas encore à jour — rien envoyé, nouvel essai", uid=q["uid"])
        return
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
        if e:
            e.status = "partial"
        log("publication partielle", uid=q["uid"], results=res)


def cmd_refresh(args):
    """Before every publication pass: compare live Shopify data with every
    future, not-fully-published product slot. If price, lot size or name
    changed (or the visual shows another price), regenerate visual and
    captions. Sets GitHub output site_changed=true so the workflow redeploys
    GitHub Pages BEFORE anything is sent to Meta."""
    from decimal import Decimal
    products = catalog.load_catalog(storefront_token=os.environ.get("SHOPIFY_STOREFRONT_TOKEN") or None)
    art_file = ROOT / "data" / "articles.json"
    articles = blog.from_dicts(json.loads(art_file.read_text())) if art_file.exists() else []
    history = History.load()
    queue = load_queue()
    today = paris_today().isoformat()
    site_changed = data_changed = False
    for q in queue:
        e = history.get(q["uid"])
        if q["status"] not in ("planned", "partial") or q["date"] < today:
            continue
        payload = q.get("payload", {})
        for key, img in (("image_sha256", payload.get("image")), ("facebook_image_sha256", payload.get("facebook_image"))):
            if img and not payload.get(key) and (ROOT / img).exists():
                payload[key] = build.file_sha256(ROOT / img)          # backfill (older items)
                data_changed = True
        if q["family"] != "product":
            continue
        p = next((x for x in products if x.handle == q["reference"]), None)
        published_somewhere = bool(e and any(v.get("status") == "published" for v in e.platforms.values()))
        if p is None or not p.available:
            if not published_somewhere:
                q["status"], q["reason"] = "skipped", "produit indisponible ou retiré de Shopify"
                if e:
                    e.status, e.error = "skipped", q["reason"]
                log("créneau annulé — produit indisponible", uid=q["uid"])
                data_changed = True
            continue
        shown = payload.get("rendered_price") or q["price"]
        if (p.price == Decimal(q["price"]) and Decimal(shown) == p.price
                and p.lot_size == q["lot_size"] and p.display_name == q["title"]):
            continue
        old = shown
        it = planner.PlanItem(**{**q, "status": "planned"})
        it.price, it.lot_size, it.title = str(p.price), p.lot_size, p.display_name
        build.build_item(it, products, articles)
        q.update({k: v for k, v in it.to_dict().items() if k != "status"})
        if e:
            e.price = it.price
        site_changed = data_changed = True
        log("visuel régénéré — prix Shopify modifié", uid=q["uid"], ancien=old, nouveau=it.price)
    if data_changed:
        history.save()
        save_queue(queue)
    if site_changed:
        write_dashboard(products, articles, history, queue)
        write_bio_page(articles, products, queue)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"site_changed={'true' if site_changed else 'false'}\n")
    print(f"refresh: visuels régénérés={site_changed}")


def cmd_rebuild(args):
    """Re-render planned, not-yet-published future items with current Shopify
    data and current settings (e.g. WhatsApp configured or not). Never touches
    an item already published on any network."""
    products = catalog.load_catalog()
    articles = blog.from_dicts(json.loads((ROOT / "data" / "articles.json").read_text()))
    history = History.load()
    queue = load_queue()
    today = paris_today().isoformat()
    for q in queue:
        e = history.get(q["uid"])
        if q["status"] != "planned" or q["date"] < today or (e and e.platforms):
            continue
        it = planner.PlanItem(**q)
        if it.family == "product":
            p = next((x for x in products if x.handle == it.reference), None)
            if not p or not p.available:
                q["status"], q["reason"] = "skipped", "produit indisponible au moment de la reconstruction"
                if e:
                    e.status, e.error = "skipped", q["reason"]
                log("créneau annulé", uid=it.uid, reason=q["reason"])
                continue
            it.price, it.lot_size, it.title = str(p.price), p.lot_size, p.display_name
        build.build_item(it, products, articles)
        q.update(it.to_dict())
        if e:
            e.price = it.price
        log("reconstruit", uid=it.uid, title=it.title)
    history.save()
    save_queue(queue)
    write_dashboard(products, articles, history, queue)
    write_bio_page(articles, products, queue)


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
    ok = all(r[1] == "OK" for r in rows)
    for name, cat, detail in rows:
        tag = {"OK": "OK         ", "CONFIGURATION": "CONFIG     ", "META": "META       ", "CODE": "ERREUR CODE"}[cat]
        print(f"[{tag}] {name} : {redact(detail)}")
    cats = {r[1] for r in rows}
    print()
    if "CODE" in cats:
        print("→ Au moins une ERREUR CODE : problème dans le programme, à corriger dans le dépôt.")
    elif "CONFIGURATION" in cats:
        print("→ Problème de CONFIGURATION (secrets, app Meta ou compte) : le code n'est pas en cause.")
    elif "META" in cats:
        print("→ Meta a refusé ou n'a pas répondu : voir le détail (souvent temporaire ou lié aux droits).")
    print("Rappel : un diagnostic réussi ne prouve pas qu'une publication réelle fonctionnera ;"
          " seule la publication de test (après accord) le prouve.")
    (ROOT / "data" / "meta_check.json").write_text(json.dumps(
        {"ts": datetime.now(TZ).isoformat(timespec="seconds"), "ok": ok,
         "checks": [{"name": n, "category": c, "detail": redact(d)} for n, c, d in rows]}, ensure_ascii=False, indent=1))
    if not ok:
        sys.exit(1)


CONFIRM_PHRASE = "JE CONFIRME LA PUBLICATION DE TEST"


def cmd_publish_test(args):
    """ONE real publication, only with the exact confirmation phrase."""
    if args.confirm != CONFIRM_PHRASE:
        sys.exit(f"Refus : phrase de confirmation attendue « {CONFIRM_PHRASE} ».")
    history = History.load()
    queue = load_queue()
    recover = getattr(args, "recover_failed", False)
    allowed = ("planned", "partial", "simulated") if args.uid else ("planned", "partial")
    if recover:
        # ONE manual catch-up of a FAILED item, Instagram only, never published before.
        if not args.uid or args.platform != "instagram":
            sys.exit("Rattrapage refusé : --uid précis et --platform instagram obligatoires.")
        allowed = ("failed",)
    cands = [q for q in queue if q["status"] in allowed and (args.uid in ("", q["uid"]))]
    if args.surface:
        cands = [q for q in cands if q["surface"] == args.surface]
    only = None if args.platform in ("", "both") else args.platform
    if only == "facebook":
        cands = [q for q in cands if q.get("facebook") and
                 (q["surface"] != "story" or q["payload"].get("facebook_image"))]
    if not cands:
        sys.exit("Aucun élément planifié correspondant dans la file.")
    q = cands[0]
    e = history.get(q["uid"])
    if recover:
        if e is None:
            sys.exit("Rattrapage refusé : élément absent de l'historique.")
        if any(v.get("status") in ("published", "uncertain") for v in e.platforms.values()):
            sys.exit("Rattrapage refusé : déjà publié ou statut incertain sur une plateforme.")
        if date.fromisoformat(q["date"]) < datetime.now(TZ).date() - timedelta(days=7):
            sys.exit("Rattrapage refusé : élément de plus de 7 jours.")
        q["status"] = "planned"                       # only for the content checks below
    products = catalog.load_catalog()
    errors = checks.check_item(planner.PlanItem(**q), products)
    if errors:
        if recover:
            q["status"] = "failed"
        sys.exit("Contrôles bloquants : " + "; ".join(errors))
    log("PUBLICATION DE TEST demandée", uid=q["uid"], title=q["title"], platform=only or "both")
    if q["status"] == "simulated":
        q["status"] = "planned"
    _publish_one(q, e, client=_client(), only=only)
    if recover:
        ig = (e.platforms if e else {}).get("instagram", {})
        if ig.get("status") == "published":
            e.platforms["facebook"] = {"status": "skipped", "reason": "rattrapage Instagram uniquement (accord explicite)"}
            q["status"], q["reason"], e.status, e.error = "published", "", "published", ""
            log("RATTRAPAGE publié sur Instagram", uid=q["uid"], id=ig.get("id"))
        elif q["status"] == "planned":                # nothing sent (e.g. stale media): back to failed
            q["status"] = "failed"
    for k, v in (e.platforms if e else {}).items():
        print(f"RÉSULTAT {k}: {v.get('status')} {v.get('permalink') or v.get('id') or v.get('error', '')}")
    history.save()
    save_queue(queue)



# --------------------------------------------------------------------- #
# Contrôle séparé par canal : Story Instagram, Post Instagram, Post Facebook
CHANNELS = {"story": [("instagram", "Story Instagram")], "post": [("instagram", "Post Instagram"), ("facebook", "Post Facebook")]}


def expected_channels(q: dict) -> list[tuple[str, str]]:
    chans = CHANNELS.get(q["surface"], [])
    if q["surface"] == "post" and not q.get("facebook"):
        chans = chans[:1]
    return chans


def channel_report(now: datetime | None = None, grace_min: int = 45, client=None) -> list[dict]:
    """Pour chaque créneau d'aujourd'hui dont l'heure + délai de grâce est passée, et pour chaque
    canal attendu : preuve de publication (identifiant Meta + lien) ou MANQUANT.
    Avec un client Meta, l'identifiant est relu chez Meta (preuve indépendante de notre fichier)."""
    now = now or datetime.now(TZ)
    history = History.load()
    rows = []
    for q in load_queue():
        if q["date"] != now.date().isoformat():
            continue
        it = planner.PlanItem(**{k: q[k] for k in planner.PlanItem.__dataclass_fields__ if k in q})
        if now < it.scheduled_at() + timedelta(minutes=grace_min):
            continue
        e = history.get(q["uid"])
        plats = e.platforms if e else {}
        for key, label in expected_channels(q):
            v = plats.get(key, {})
            ok = v.get("status") == "published" and bool(v.get("id"))
            link = v.get("permalink", "")
            if ok and client is not None:
                try:
                    link = (client.ig_permalink(v["id"]) if key == "instagram" else client.fb_permalink(v["id"])) or link
                    confirmed = "confirmé par Meta" if link or q["surface"] == "story" else "identifiant non relu"
                except Exception as exc:                    # never fail open: report it
                    confirmed = f"relecture Meta impossible ({type(exc).__name__})"
            else:
                confirmed = ""
            rows.append({"uid": q["uid"], "heure": q["time"], "canal": label, "ok": ok,
                         "statut": v.get("status") or q.get("status"), "id": v.get("id", ""), "lien": link,
                         "verification": confirmed, "raison": v.get("error") or q.get("reason", "")})
    return rows


def cmd_check(args):
    client = None
    if os.environ.get("META_SYSTEM_USER_TOKEN"):
        try:
            client = _client()
        except SystemExit:
            client = None
    rows = channel_report(grace_min=args.grace, client=client)
    missing = [r for r in rows if not r["ok"]]
    lines = [f"{'OK ' if r['ok'] else 'MANQUANT'} {r['heure']} {r['canal']:<16} {r['uid']}  id={r['id'] or '-'}  {r['lien'] or ''} {r['verification']} {r['raison']}".rstrip()
             for r in rows] or ["Aucun créneau échu à contrôler pour l'instant."]
    print("\n".join(lines))
    out = os.environ.get("GITHUB_STEP_SUMMARY")
    if out:
        with open(out, "a") as fh:
            fh.write("### Contrôle des publications du jour\n\n" + "\n".join(f"- {l}" for l in lines) + "\n")
    if missing:
        Path(ROOT / "data" / "alerte.txt").write_text("\n".join(l for l in lines if l.startswith("MANQUANT")) + "\n")
        sys.exit(f"{len(missing)} publication(s) manquante(s) aujourd'hui : voir le résumé de l'exécution")


# --------------------------------------------------------------------- #
CATCHUP_PHRASE = "JE CONFIRME LE RATTRAPAGE"


def cmd_catchup(args):
    """Rattrapage manuel d'UN canal d'UN créneau manqué ou en échec, sans doublon :
    refus si déjà publié / incertain sur ce canal, si plus de 2 jours, ou si un post
    identique est déjà visible chez Meta."""
    if args.confirm != CATCHUP_PHRASE:
        sys.exit(f"Refus : phrase de confirmation attendue « {CATCHUP_PHRASE} ».")
    queue, history = load_queue(), History.load()
    q = next((x for x in queue if x["uid"] == args.uid), None)
    if q is None:
        sys.exit("Rattrapage refusé : créneau absent de la file.")
    if q["status"] not in ("missed", "failed", "partial", "planned"):
        sys.exit(f"Rattrapage refusé : statut « {q['status']} ».")
    if args.platform == "facebook" and not (q["surface"] == "post" and q.get("facebook")):
        sys.exit("Rattrapage refusé : ce créneau n'est pas prévu sur Facebook.")
    if date.fromisoformat(q["date"]) < paris_today() - timedelta(days=2):
        sys.exit("Rattrapage refusé : créneau de plus de 2 jours.")
    e = history.get(q["uid"])
    v = (e.platforms if e else {}).get(args.platform, {})
    if v.get("status") in ("published", "uncertain"):
        sys.exit(f"Rattrapage refusé : déjà {v.get('status')} sur {args.platform} (id {v.get('id', '-')}).")
    client = _client()
    first = (q["payload"].get(f"caption_{args.platform}") or q.get("title", "")).splitlines()[0].strip()
    if first and q["surface"] == "post":
        recent = client.ig_recent_media(25) if args.platform == "instagram" else client.fb_recent_posts(25)
        txt = "caption" if args.platform == "instagram" else "message"
        if any(first in (m.get(txt) or "") for m in recent):
            sys.exit("Rattrapage refusé : une publication identique est déjà visible chez Meta.")
    prev = q["status"]
    q["status"] = "planned"
    errors = checks.check_item(planner.PlanItem(**q), catalog.load_catalog())
    if errors:
        q["status"] = prev
        save_queue(queue)
        sys.exit("Contrôles bloquants : " + "; ".join(errors))
    if e and e.status == "missed":
        e.status = "planned"
    log("RATTRAPAGE demandé", uid=q["uid"], platform=args.platform)
    _publish_one(q, e, client=client, only=args.platform)
    r = (e.platforms if e else {}).get(args.platform, {})
    print(f"RÉSULTAT {args.platform}: {r.get('status')} id={r.get('id', '-')} {r.get('permalink', '')}")
    history.save()
    save_queue(queue)
    if r.get("status") != "published":
        sys.exit("Rattrapage non confirmé : voir data/log.jsonl")

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


def write_bio_page(articles, products, queue=None):
    """Link-in-bio page (real, clickable links): WhatsApp engraving, this
    week's featured products and articles, shop, contact. Instagram captions
    point here ("lien en bio")."""
    esc = html.escape
    overrides = planner.load_json(planner.BLOG_OVERRIDES)
    enabled = [a for a in blog.usable(articles) if overrides.get(a.handle, {}).get("enabled", True)]
    wa = whatsapp.link_or_none("gravure")
    sections = []
    if wa:
        sections.append(("Gravure personnalisée", [("Renseignements sur WhatsApp", wa)]))
    feat, seen = [], set()
    today = paris_today().isoformat()
    for q in sorted(queue or [], key=lambda q: q["date"], reverse=True):
        if q.get("status") in ("planned", "published", "partial", "simulated") and q.get("url") \
                and q["date"] >= (paris_today() - timedelta(days=14)).isoformat() and q["url"] not in seen:
            if q["family"] == "product" and q["date"] <= today or q["family"] == "blog" and q["date"] <= today:
                label = q["title"] if q["family"] == "blog" else f'{q["title"]} — lot de {q["lot_size"]}'
                feat.append((label, q["url"]))
                seen.add(q["url"])
    if feat:
        sections.append(("Publications récentes", feat[:6]))
    sections.append(("Articles", [(a.title, a.url) for a in sorted(enabled, key=lambda a: a.published, reverse=True)[:5]]))
    sections.append(("Le Comptoir du Rotatif", [("Boutique en ligne", "https://lecomptoirdurotatif.com"),
                                                ("Nous contacter", "https://lecomptoirdurotatif.com/pages/contact")]))
    body = "".join(f'<h2>{esc(t)}</h2>' + "".join(f'<a class="l" href="{esc(u)}">{esc(lbl)}</a>' for lbl, u in links)
                   for t, links in sections if links)
    page = f'''<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Le Comptoir du Rotatif — liens</title><style>body{{margin:0;font:16px/1.4 system-ui,sans-serif;background:#fff;color:#181b20}}
main{{max-width:480px;margin:auto;padding:40px 20px 60px;text-align:center}}img{{height:64px;margin-bottom:8px}}
h2{{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:#7a7e85;font-weight:600;margin:28px 0 8px}}
.l{{display:block;margin:10px 0;padding:14px 16px;border:1px solid #181b20;border-radius:4px;color:#181b20;text-decoration:none}}</style></head>
<body><main><img src="../logo.png" alt="Le Comptoir du Rotatif"><div>Instruments rotatifs dentaires</div>{body}</main></body></html>'''
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
    d.add_argument("--mode", choices=["test", "live", "auto"], default=os.environ.get("PUBLISH_MODE", "test"))
    sub.add_parser("rebuild")
    sub.add_parser("refresh")
    c = sub.add_parser("meta-check")
    c.add_argument("--container-test", action="store_true")
    k = sub.add_parser("check")
    k.add_argument("--grace", type=int, default=45)
    r = sub.add_parser("catchup")
    r.add_argument("--uid", required=True)
    r.add_argument("--platform", choices=["instagram", "facebook"], required=True)
    r.add_argument("--confirm", default="")
    t = sub.add_parser("publish-test")
    t.add_argument("--confirm", default="")
    t.add_argument("--uid", default="")
    t.add_argument("--surface", choices=["", "post", "story"], default="")
    t.add_argument("--platform", choices=["", "both", "instagram", "facebook"], default="both")
    t.add_argument("--recover-failed", action="store_true")
    args = ap.parse_args(argv)
    {"weekly": cmd_weekly, "due": cmd_due, "meta-check": cmd_meta_check, "rebuild": cmd_rebuild, "refresh": cmd_refresh,
     "publish-test": cmd_publish_test, "check": cmd_check, "catchup": cmd_catchup}[args.cmd](args)


if __name__ == "__main__":
    main()
