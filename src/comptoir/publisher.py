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


def public_url(base_url: str, rel_path: str) -> str:
    """site/media/x.jpg -> https://<user>.github.io/<repo>/media/x.jpg"""
    rel = rel_path.split("site/", 1)[-1] if rel_path.startswith("site/") else rel_path
    return base_url.rstrip("/") + "/" + rel.lstrip("/")


def verify_public_image(url: str, expected_size: tuple[int, int] | None = None, get=None) -> dict:
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
        verify_public_image(url, SIZES["story" if story else "portrait"], get=get)
        caption = "" if story else payload.get("caption_instagram", "")
        started = datetime.now(timezone.utc) - timedelta(minutes=2)
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

    # -- Facebook ---------------------------------------------------------
    if item.get("facebook") and "facebook" not in done:
        img = payload.get("facebook_image") or payload["image"]
        if story and not payload.get("facebook_image"):
            return results                          # no vertical story on the Page feed
        url = public_url(base_url, img)
        verify_public_image(url, SIZES["portrait"], get=get)
        caption = payload.get("caption_facebook", "")
        started = datetime.now(timezone.utc) - timedelta(minutes=2)
        try:
            body = client.fb_photo(url, caption, published=True)
            pid = body.get("post_id") or body.get("id")
        except AmbiguousPublish as exc:
            found = _find_recent(client.fb_recent_posts(), caption, "message", started)
            if not found:
                results["facebook"] = {"status": "uncertain", "error": str(exc)}
                return results
            pid = found["id"]
        results["facebook"] = {"status": "published", "id": pid, "permalink": client.fb_permalink(pid)}
    return results


# --------------------------------------------------------------------------- #
# Diagnostics — NO publication
# --------------------------------------------------------------------------- #
def diagnose(client: MetaClient, base_url: str | None, sample_image: str | None = None,
             container_test: bool = False, get=None) -> list[tuple[str, bool, str]]:
    """Each check -> (name, ok, detail). Never publishes anything.
    container_test=True creates an Instagram container that is NOT published
    (it expires by itself after 24 h) to prove Meta can fetch our image."""
    out: list[tuple[str, bool, str]] = []

    def check(name, fn):
        try:
            detail = fn()
            out.append((name, True, detail or "OK"))
            return True
        except Exception as exc:              # report, continue
            out.append((name, False, str(exc)))
            return False

    check("Jeton système valide", lambda: f"identité {client.call('GET', 'me', fields='id,name').get('name', '?')}")
    check("Autorisations", lambda: _scopes(client))
    if not check("Accès à la Page (jeton de Page dérivé)", lambda: (client.page_token(), "OK")[1]):
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
        out.append(("Image publique (GitHub Pages)", False, "PUBLIC_BASE_URL ou image d'exemple absente"))
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
