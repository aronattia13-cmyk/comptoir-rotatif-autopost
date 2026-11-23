"""Publication of a plan item on Instagram (post / story) and Facebook (post).

Safety rules
- The image must be publicly reachable (HTTP 200, image/jpeg, < 8 MB, right
  pixel size) on GitHub Pages *before* anything is sent to Meta.
- Per-platform idempotency: a platform already marked done in history is
  never published again; if Instagram succeeded and Facebook failed, only
  Facebook is retried later.
- Ambiguous outcome (timeout after the publish call): we look for the post in
  the account's recent media; if not found the item is marked `uncertain`
  and is NOT retried automatically (better a missing post than a duplicate).
"""
from __future__ import annotations

import hashlib
import io
from datetime import datetime, timedelta, timezone

import requests
from PIL import Image

from . import net
from .meta import AmbiguousPublish, MetaClient, MetaError, PORTFOLIO_SCOPES, REQUIRED_SCOPES

MAX_BYTES = 8 * 1024 * 1024
SIZES = {"portrait": (1080, 1350), "story": (1080, 1920), "square": (1080, 1080)}


class MediaNotPublic(RuntimeError):
    pass


class StaleMedia(MediaNotPublic):
    """GitHub Pages still serves an older version of the visual."""


def public_url(base_url: str, rel_path: str) -> str:
    """site/media/x.jpg -> https://<user>.github.io/<repo>/media/x.jpg"""
    rel = rel_path.split("site/", 1)[-1] if rel_path.startswith("site/") else rel_path
    return base_url.rstrip("/") + "/" + rel.lstrip("/")


def verify_public_image(url: str, expected_size: tuple[int, int] | None = None, get=None,
                        expected_sha256: str | None = None) -> dict:
    get = get or (lambda u: net.get(u, timeout=30, retries=2))
    try:
        r = get(url)
    except requests.RequestException as exc:
        raise MediaNotPublic(f"image injoignable ({exc.__class__.__name__}): {url}") from exc
    ctype = r.headers.get("content-type", "")
    if r.status_code != 200:
        raise MediaNotPublic(f"HTTP {r.status_code}: {url}")
    if not ctype.startswith("image/jpeg"):
        raise MediaNotPublic(f"type {ctype or 'inconnu'} au lieu de image/jpeg: {url}")
    if len(r.content) > MAX_BYTES:
        raise MediaNotPublic(f"image trop lourde ({len(r.content)} octets)")
    if expected_sha256 and hashlib.sha256(r.content).hexdigest() != expected_sha256:
        raise StaleMedia(f"image publique pas à jour (ancienne version encore en ligne) : {url}")
    with Image.open(io.BytesIO(r.content)) as im:
        if im.format != "JPEG":
            raise MediaNotPublic(f"format {im.format}")
        if expected_size and im.size != tuple(expected_size):
            raise MediaNotPublic(f"dimensions {im.size} au lieu de {expected_size}")
        return {"url": url, "bytes": len(r.content), "size": im.size}


def _caption_key(text: str) -> str:
    return " ".join((text or "").split())[:120]


def _find_recent(posts: list[dict], caption: str, field: str, since: datetime) -> dict | None:
    key = _caption_key(caption)
    for p in posts:
        ts = p.get("timestamp") or p.get("created_time") or ""
        try:
            when = datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if when >= since and _caption_key(p.get(field, "")) == key:
            return p
    return None


def _already_on_meta(client, caption: str, platform: str, hours: int = 36) -> dict | None:
    """Garde anti-doublon AVANT envoi (posts seulement) : si une publication à la légende identique
    existe déjà chez Meta (ex. deux exécutions rapprochées, historique non enregistré), on ne republie pas."""
    if not caption:
        return None
    try:
        posts = client.ig_recent_media(25) if platform == "instagram" else client.fb_recent_posts(25)
    except Exception:                       # lecture impossible : comportement inchangé (envoi normal)
        return None
    field = "caption" if platform == "instagram" else "message"
    return _find_recent(posts or [], caption, field, datetime.now(timezone.utc) - timedelta(hours=hours))


def publish_item(item: dict, client: MetaClient, base_url: str, done: dict,
                 poll: int = 5, timeout: int = 180, get=None) -> dict:
    """Publish one queue item. `done` = platforms already recorded in history
    for this uid. Returns {platform: {...}} for what happened now."""
    results: dict = {}
    payload = item["payload"]
    story = item["surface"] == "story"

    # -- Instagram --------------------------------------------------------
    if "instagram" not in done:
        url = public_url(base_url, payload["image"])
        verify_public_image(url, SIZES["story" if story else "portrait"], get=get,
                            expected_sha256=payload.get("image_sha256"))
        caption = "" if story else payload.get("caption_instagram", "")
        started = datetime.now(timezone.utc) - timedelta(minutes=2)
        dup = None if story else _already_on_meta(client, caption, "instagram")
        if dup:
            results["instagram"] = {"status": "published", "id": dup["id"], "permalink": dup.get("permalink", ""),
                                    "note": "déjà présent chez Meta : non republié"}
        else:
            results.update(_ig_send(client, url, caption, story, started, poll, timeout))
            if results["instagram"]["status"] == "uncertain":
                return results

    # -- Facebook ---------------------------------------------------------
    if item.get("facebook") and "facebook" not in done:
        img = payload.get("facebook_image") or payload["image"]
        if story and not payload.get("facebook_image"):
            return results                          # no vertical story on the Page feed
        caption = payload.get("caption_facebook", "")
        dup = _already_on_meta(client, caption, "facebook")
        if dup:
            results["facebook"] = {"status": "published", "id": dup["id"], "permalink": dup.get("permalink_url", ""),
                                   "note": "déjà présent chez Meta : non republié"}
            return results
        return _fb_send(client, base_url, payload, img, caption, results, get)
    return results


def _ig_send(client, url, caption, story, started, poll, timeout) -> dict:
    results: dict = {}
    cid = client.ig_create_container(url, caption, story=story)
    client.ig_wait_container(cid, poll=poll, timeout=timeout)
    try:
        mid = client.ig_publish(cid)
    except AmbiguousPublish as exc:
        found = None if story else _find_recent(client.ig_recent_media(), caption, "caption", started)
        if not found:
            results["instagram"] = {"status": "uncertain", "container": cid, "error": str(exc)}
            return results
        mid = found["id"]
    results["instagram"] = {"status": "published", "id": mid, "container": cid,
                            "permalink": client.ig_permalink(mid)}
    return results


def _fb_send(client, base_url, payload, img, caption, results, get) -> dict:
    url = public_url(base_url, img)
    sha = payload.get("facebook_image_sha256") if payload.get("facebook_image") else payload.get("image_sha256")
    started = datetime.now(timezone.utc) - timedelta(minutes=2)
    try:
        verify_public_image(url, SIZES["portrait"], get=get, expected_sha256=sha)
        body = client.fb_photo(url, caption, published=True)
        pid = body.get("post_id") or body.get("id")
    except AmbiguousPublish as exc:
        found = _find_recent(client.fb_recent_posts(), caption, "message", started)
        if not found:
            results["facebook"] = {"status": "uncertain", "error": str(exc)}
            return results
        pid = found["id"]
    except StaleMedia as exc:
        # Keep what Instagram already did; Facebook is retried after redeploy.
        results["facebook"] = {"status": "retry", "error": str(exc)}
        return results
    except (MetaError, MediaNotPublic) as exc:
        if not results:
            raise
        results["facebook"] = {"status": "failed", "error": str(exc)}
        return results
    results["facebook"] = {"status": "published", "id": pid, "permalink": client.fb_permalink(pid)}
    return results


# --------------------------------------------------------------------------- #
# Diagnostics — NO publication
# --------------------------------------------------------------------------- #
OK, CONFIG, META, CODE = "OK", "CONFIGURATION", "META", "CODE"


def _category(exc: Exception) -> str:
    from .meta import MetaConfigError
    if isinstance(exc, (MetaConfigError, MediaNotPublic)):
        return CONFIG
    if isinstance(exc, MetaError):
        return META
    return CODE                                    # anything else = a bug in our code


def diagnose(client: MetaClient, base_url: str | None, sample_image: str | None = None,
             container_test: bool = False, get=None) -> list[tuple[str, str, str]]:
    """Each check -> (name, category, detail). category: OK | CONFIGURATION
    (secrets, app or account settings) | META (API refused / unavailable) |
    CODE (bug in this program). Never publishes anything.
    container_test=True creates an Instagram container that is NOT published
    (it expires by itself after 24 h) to prove Meta can fetch our image."""
    from . import meta as m
    out: list[tuple[str, str, str]] = []

    def check(name, fn):
        try:
            out.append((name, OK, fn() or "OK"))
            return True
        except Exception as exc:              # report, continue
            out.append((name, _category(exc), m.redact(f"{exc.__class__.__name__}: {exc}")
                        if _category(exc) == CODE else m.redact(str(exc))))
            return False

    cfg = client.cfg

    def _formats():
        parts = (cfg.notes or []) + m.describe_secret_formats(cfg.token, cfg.app_secret)
        if cfg.app_secret and not (len(cfg.app_secret) == 32 and all(c in "0123456789abcdef" for c in cfg.app_secret)):
            raise m.MetaConfigError(" ; ".join(parts))
        return " ; ".join(parts) or "aucun secret à normaliser"
    check("Format des secrets (sans afficher les valeurs)", _formats)

    def _kat():
        if not m.self_test_signature():
            raise RuntimeError("le calcul HMAC-SHA256 ne donne pas le résultat de référence")
        return "HMAC-SHA256 conforme au vecteur de référence"
    if not check("Calcul de la signature appsecret_proof (auto-test du code)", _kat):
        return out

    if cfg.app_id:
        def _owner():
            d = client.debug_token()
            if not d.get("is_valid"):
                raise m.MetaConfigError(f"jeton invalide selon Meta ({(d.get('error') or {}).get('message', 'sans détail')})")
            if str(d.get("app_id")) != str(cfg.app_id):
                raise m.MetaConfigError("le jeton a été généré pour une AUTRE application que META_APP_ID : "
                                        "régénérer le jeton avec la bonne app, ou corriger META_APP_ID/META_APP_SECRET")
            exp = d.get("expires_at", 0)
            return (f"jeton émis par l'app META_APP_ID ✔ ; type {d.get('type', '?')} ; "
                    f"expiration {'jamais' if not exp else exp} ; la clé secrète correspond à l'app ✔")
        check("Correspondance jeton ↔ application ↔ clé secrète", _owner)
    else:
        out.append(("Correspondance jeton ↔ application ↔ clé secrète", CONFIG,
                    "variable META_APP_ID absente : ajoutez-la pour un diagnostic précis (valeur publique)"))

    def _me():
        try:
            return f"identité {client.call('GET', 'me', fields='id,name').get('name', '?')} (requête signée ✔)"
        except m.MetaError as exc:
            if "appsecret_proof" not in str(exc).lower():
                raise
            # Diagnostic probe ONLY: same request without signature.
            try:
                client.call("GET", "me", sign=False, retries=0, fields="id")
                verdict = ("le jeton fonctionne SANS signature mais pas AVEC : la clé secrète META_APP_SECRET "
                           "n'appartient pas à l'application qui a généré le jeton (configuration, pas le code)")
            except m.MetaError as exc2:
                if "appsecret_proof" in str(exc2).lower() or "app secret" in str(exc2).lower():
                    verdict = ("« Exiger la clé secrète » est actif et la signature est refusée : la clé secrète "
                               "ne correspond pas à l'app du jeton (le calcul du code est vérifié par l'auto-test)")
                else:
                    verdict = f"le jeton lui-même est refusé ({exc2})"
            raise m.MetaConfigError(verdict) from exc
    if not check("Jeton système valide", _me):
        return out
    check("Autorisations", lambda: _scopes(client))
    if not check("Accès à la Page (jeton de Page dérivé, signé séparément)", lambda: (client.page_token(), "OK")[1]):
        return out
    check("Page Facebook", lambda: client.page_info().get("name", "?"))
    check("Compte Instagram lié", lambda: f"IG id {client.ig_user_id()}")
    check("Quota de publication Instagram", lambda: str(client.publishing_limit()))
    if base_url and sample_image:
        url = public_url(base_url, sample_image)
        ok = check("Image publique (GitHub Pages)", lambda: str(verify_public_image(url, get=get)))
        if ok and container_test:
            def _container():
                cid = client.ig_create_container(url, "", story=False)
                st = client.ig_wait_container(cid, timeout=120)
                return f"conteneur {cid} {st} — NON publié (expire seul sous 24 h)"
            check("Conteneur Instagram de test (non publié)", _container)
    else:
        out.append(("Image publique (GitHub Pages)", CONFIG, "PUBLIC_BASE_URL ou image d'exemple absente"))
    return out


def _scopes(client: MetaClient) -> str:
    granted = client.permissions()
    missing = REQUIRED_SCOPES - granted
    if missing:
        raise MetaError(f"autorisations manquantes : {', '.join(sorted(missing))}")
    extra = granted - REQUIRED_SCOPES - PORTFOLIO_SCOPES - {"public_profile"}
    note = ""
    if PORTFOLIO_SCOPES - granted:
        note += f" | attention : {', '.join(sorted(PORTFOLIO_SCOPES - granted))} absent(s) (exigé(s) par Meta si l'accès à la Page passe par le portefeuille)"
    if extra:
        note += f" | autorisations superflues à retirer : {', '.join(sorted(extra))}"
    return ", ".join(sorted(granted & (REQUIRED_SCOPES | PORTFOLIO_SCOPES))) + note
