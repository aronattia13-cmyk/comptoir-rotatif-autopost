"""Meta publishing — fully mocked (no network, no real account)."""
import io

import pytest
import requests
from PIL import Image

from comptoir import meta, publisher
from comptoir.meta import AmbiguousPublish, MetaClient, MetaConfig, MetaError


class Resp:
    def __init__(self, status=200, body=None, content=b"", headers=None):
        self.status_code, self._body = status, body if body is not None else {}
        self.content, self.headers = content, headers or {}
        self.ok = 200 <= status < 300
        self.text = str(body)

    def json(self):
        return self._body


class FakeSession:
    """Routes (method, path-suffix) -> list of responses (consumed in order)."""
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def _hit(self, method, url, data):
        path = url.split("/v25.0/", 1)[1]
        self.calls.append((method, path, dict(data)))
        for (m, suffix), resps in self.routes.items():
            if m == method and path.endswith(suffix):
                r = resps.pop(0) if len(resps) > 1 else resps[0]
                if isinstance(r, Exception):
                    raise r
                return r
        raise AssertionError(f"route inattendue {method} {path}")

    def get(self, url, params=None, timeout=None):
        return self._hit("GET", url, params)

    def post(self, url, data=None, timeout=None):
        return self._hit("POST", url, data)


def jpeg(size=(1080, 1350)):
    b = io.BytesIO()
    Image.new("RGB", size, "white").save(b, "JPEG")
    return b.getvalue()


def ok_get(size=(1080, 1350)):
    return lambda url: Resp(200, content=jpeg(size), headers={"content-type": "image/jpeg"})


BASE = {
    ("GET", "PAGE1"): [Resp(200, {"access_token": "PAGETOKEN123", "name": "LCDR"})],
    ("POST", "IG1/media"): [Resp(200, {"id": "C1"})],
    ("GET", "C1"): [Resp(200, {"status_code": "IN_PROGRESS"}), Resp(200, {"status_code": "FINISHED"})],
    ("POST", "IG1/media_publish"): [Resp(200, {"id": "M1"})],
    ("GET", "M1"): [Resp(200, {"permalink": "https://instagram.com/p/x"})],
    ("POST", "PAGE1/photos"): [Resp(200, {"id": "PH1", "post_id": "PAGE1_P1"})],
    ("GET", "PAGE1_P1"): [Resp(200, {"permalink_url": "https://facebook.com/p1"})],
}


def client(routes=None, secret=None):
    r = {k: list(v) for k, v in (routes or BASE).items()}
    cfg = MetaConfig(token="SYSTEMTOKEN123", page_id="PAGE1", ig_user_id="IG1", app_secret=secret)
    return MetaClient(cfg, session=FakeSession(r), sleep=lambda s: None)


def item(surface="post", facebook=True, fb_image=None):
    p = {"image": "site/media/x.jpg", "caption_instagram": "Légende IG", "caption_facebook": "Légende FB"}
    if fb_image:
        p["facebook_image"] = fb_image
    return {"uid": "2026-11-16-mon-product", "surface": surface, "facebook": facebook, "payload": p}


# ------------------------------------------------------------------ media ----
def test_public_url():
    assert publisher.public_url("https://u.github.io/repo/", "site/media/a.jpg") == \
        "https://u.github.io/repo/media/a.jpg"


def test_media_check_rejects_png_and_404_and_wrong_size():
    with pytest.raises(publisher.MediaNotPublic):
        publisher.verify_public_image("u", get=lambda u: Resp(404))
    with pytest.raises(publisher.MediaNotPublic):
        publisher.verify_public_image("u", get=lambda u: Resp(200, content=b"x", headers={"content-type": "image/png"}))
    with pytest.raises(publisher.MediaNotPublic):
        publisher.verify_public_image("u", (1080, 1920), get=ok_get((1080, 1350)))
    assert publisher.verify_public_image("u", (1080, 1350), get=ok_get())["size"] == (1080, 1350)


# ---------------------------------------------------------------- publish ----
def test_post_published_on_both_platforms():
    c = client()
    res = publisher.publish_item(item(), c, "https://u.github.io/r", done=set(), poll=1, get=ok_get())
    assert res["instagram"]["status"] == "published" and res["instagram"]["permalink"]
    assert res["facebook"]["status"] == "published" and res["facebook"]["id"] == "PAGE1_P1"
    posted = [x for x in c.s.calls if x[0] == "POST"]
    assert posted[0][2]["image_url"] == "https://u.github.io/r/media/x.jpg"
    assert posted[0][2]["caption"] == "Légende IG"


def test_story_uses_stories_type_without_caption_and_no_fb_feed():
    c = client()
    res = publisher.publish_item(item("story"), c, "https://u.github.io/r", set(), poll=1, get=ok_get((1080, 1920)))
    create = next(x for x in c.s.calls if x[1].endswith("IG1/media"))
    assert create[2]["media_type"] == "STORIES" and "caption" not in create[2]
    assert "facebook" not in res


def test_already_published_platform_is_skipped():
    c = client()
    res = publisher.publish_item(item(), c, "https://u.github.io/r", done={"instagram"}, poll=1, get=ok_get())
    assert "instagram" not in res and res["facebook"]["status"] == "published"
    assert not any(x[1].endswith("IG1/media") for x in c.s.calls)


def test_image_not_public_blocks_before_meta():
    c = client()
    with pytest.raises(publisher.MediaNotPublic):
        publisher.publish_item(item(), c, "https://u.github.io/r", set(), get=lambda u: Resp(404))
    assert not any(x[0] == "POST" for x in c.s.calls)


def test_container_error_stops():
    routes = dict(BASE)
    routes[("GET", "C1")] = [Resp(200, {"status_code": "ERROR"})]
    with pytest.raises(MetaError):
        publisher.publish_item(item(), client(routes), "https://u.github.io/r", set(), poll=1, get=ok_get())


def test_container_timeout():
    routes = dict(BASE)
    routes[("GET", "C1")] = [Resp(200, {"status_code": "IN_PROGRESS"})]
    with pytest.raises(MetaError, match="après"):
        publisher.publish_item(item(), client(routes), "https://u.github.io/r", set(), poll=5, timeout=10,
                               get=ok_get())


def test_ambiguous_publish_found_in_recent_media():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")
    routes = dict(BASE)
    routes[("POST", "IG1/media_publish")] = [requests.Timeout()]
    routes[("GET", "IG1/media")] = [Resp(200, {"data": [{"id": "M1", "caption": "Légende IG", "timestamp": now}]})]
    res = publisher.publish_item(item(facebook=False), client(routes), "https://u.github.io/r", set(),
                                 poll=1, get=ok_get())
    assert res["instagram"]["status"] == "published" and res["instagram"]["id"] == "M1"


def test_ambiguous_publish_not_found_is_uncertain_and_not_retried():
    routes = dict(BASE)
    routes[("POST", "IG1/media_publish")] = [requests.Timeout()]
    routes[("GET", "IG1/media")] = [Resp(200, {"data": []})]
    c = client(routes)
    res = publisher.publish_item(item(), c, "https://u.github.io/r", set(), poll=1, get=ok_get())
    assert res == {"instagram": {"status": "uncertain", "container": "C1", "error": res["instagram"]["error"]}}
    assert sum(1 for x in c.s.calls if x[1].endswith("media_publish")) == 1    # no blind retry
    assert not any(x[1].endswith("photos") for x in c.s.calls)


def test_transient_get_is_retried_permanent_error_is_not():
    routes = dict(BASE)
    routes[("GET", "C1")] = [Resp(500, {"error": {"code": 2, "message": "tmp"}}), Resp(200, {"status_code": "FINISHED"})]
    publisher.publish_item(item(facebook=False), client(routes), "https://u.github.io/r", set(), poll=1, get=ok_get())
    routes = dict(BASE)
    routes[("POST", "IG1/media")] = [Resp(400, {"error": {"code": 100, "message": "Invalid parameter"}})]
    with pytest.raises(MetaError) as e:
        publisher.publish_item(item(), client(routes), "https://u.github.io/r", set(), get=ok_get())
    assert not e.value.transient


# --------------------------------------------------------------- security ----
def test_tokens_never_in_error_messages():
    meta.register_secret("SYSTEMTOKEN123")
    meta.register_secret("PAGETOKEN123")
    routes = dict(BASE)
    routes[("POST", "IG1/media")] = [Resp(400, {"error": {"code": 190, "message": "bad token SYSTEMTOKEN123 PAGETOKEN123"}})]
    with pytest.raises(MetaError) as e:
        publisher.publish_item(item(), client(routes), "https://u.github.io/r", set(), get=ok_get())
    assert "SYSTEMTOKEN123" not in str(e.value) and "PAGETOKEN123" not in str(e.value)


def test_appsecret_proof_added():
    c = client(secret="APPSECRET999")
    c.page_token()
    assert "appsecret_proof" in c.s.calls[0][2]


def test_config_requires_secrets(monkeypatch):
    monkeypatch.delenv("META_SYSTEM_USER_TOKEN", raising=False)
    monkeypatch.delenv("META_PAGE_ID", raising=False)
    with pytest.raises(MetaError, match="manquants"):
        MetaConfig.from_env()


def test_page_not_assigned():
    routes = dict(BASE)
    routes[("GET", "PAGE1")] = [Resp(200, {"name": "LCDR"})]
    with pytest.raises(MetaError, match="assignée"):
        client(routes).page_token()


# ------------------------------------------------------------ diagnostics ----
def test_diagnose_never_publishes():
    routes = dict(BASE)
    routes[("GET", "me")] = [Resp(200, {"id": "1", "name": "bot"})]
    routes[("GET", "me/permissions")] = [Resp(200, {"data": [{"permission": p, "status": "granted"}
                                                              for p in meta.REQUIRED_SCOPES]})]
    routes[("GET", "IG1/content_publishing_limit")] = [Resp(200, {"data": [{"quota_usage": 0}]})]
    c = client(routes)
    c.cfg.ig_user_id = "IG1"
    routes[("GET", "PAGE1")] = [Resp(200, {"access_token": "PAGETOKEN123", "name": "LCDR",
                                           "instagram_business_account": {"id": "IG1"}})]
    c.s.routes = {k: list(v) for k, v in routes.items()}
    rows = publisher.diagnose(c, "https://u.github.io/r", "site/media/x.jpg", container_test=True, get=ok_get())
    assert all(cat == "OK" for name, cat, _ in rows if "META_APP_ID" not in _), rows
    assert not any(x[1].endswith(("media_publish", "photos")) for x in c.s.calls)


def test_publish_test_requires_exact_phrase():
    from comptoir import run
    with pytest.raises(SystemExit, match="Refus"):
        run.main(["publish-test", "--confirm", "oui"])


def test_live_mode_locked_without_flag(monkeypatch):
    from comptoir import run
    assert not run.LIVE_FLAG.exists()                 # isolated fake path (conftest)
    monkeypatch.setattr(run, "_client", lambda: pytest.fail("Meta client must not be built"))
    with pytest.raises(SystemExit, match="verrouillée"):
        run.main(["due", "--mode", "live"])
