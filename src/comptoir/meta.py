"""Meta Graph API client — Instagram API with Facebook Login + Facebook Pages.

Token model (no expiry to babysit):
  META_SYSTEM_USER_TOKEN  System User token from the Business portfolio
                          (Business settings > Users > System users), expiry "Never",
                          assigned to the Page and the Instagram account.
  At runtime the client derives the Page access token
  (GET /{page-id}?fields=access_token) which both Instagram publishing
  (Facebook Login flavour) and Facebook Page posts require.
  META_APP_SECRET (optional, recommended) adds appsecret_proof to every call.

Secrets come from the environment (GitHub Actions Secrets) only. They are
never logged: every error message passes through `redact()`.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from dataclasses import dataclass

import requests

GRAPH = "https://graph.facebook.com"

# Meta error codes worth a retry (throttling / transient)
TRANSIENT_CODES = {1, 2, 4, 17, 32, 341, 368, 613, 9004, 2207051}
# Facebook-Login permission family (the only one System User tokens support;
# the instagram_business_* family belongs to Instagram Login, not used here).
REQUIRED_SCOPES = {"instagram_basic", "instagram_content_publish", "pages_show_list",
                   "pages_read_engagement", "pages_manage_posts"}
# Required by Meta for IG publishing when Page access comes through the
# Business portfolio — which is the case for a System User.
PORTFOLIO_SCOPES = {"ads_management", "ads_read"}


class MetaError(RuntimeError):
    def __init__(self, message, code=None, subcode=None, transient=False, status=None):
        super().__init__(message)
        self.code, self.subcode, self.transient, self.status = code, subcode, transient, status


class AmbiguousPublish(MetaError):
    """The publish call may or may not have gone through: never retry blindly."""


_SECRETS: list[str] = []


def register_secret(value: str | None):
    if value and len(value) >= 6:
        _SECRETS.append(value)


def redact(text: str) -> str:
    for s in _SECRETS:
        text = text.replace(s, "***")
    return text


@dataclass
class MetaConfig:
    token: str
    page_id: str
    ig_user_id: str | None = None
    app_secret: str | None = None
    version: str = "v25.0"

    @classmethod
    def from_env(cls, version: str = "v25.0") -> "MetaConfig":
        missing = [k for k in ("META_SYSTEM_USER_TOKEN", "META_PAGE_ID") if not os.environ.get(k)]
        if missing:
            raise MetaError(f"secrets GitHub manquants : {', '.join(missing)}")
        cfg = cls(token=os.environ["META_SYSTEM_USER_TOKEN"], page_id=os.environ["META_PAGE_ID"].strip(),
                  ig_user_id=(os.environ.get("META_IG_USER_ID") or "").strip() or None,
                  app_secret=os.environ.get("META_APP_SECRET") or None, version=version)
        register_secret(cfg.token)
        register_secret(cfg.app_secret)
        return cfg


class MetaClient:
    def __init__(self, cfg: MetaConfig, session: requests.Session | None = None,
                 sleep=time.sleep, timeout: int = 60):
        self.cfg = cfg
        self.s = session or requests.Session()
        self.sleep = sleep
        self.timeout = timeout
        self._page_token: str | None = None

    # ------------------------------------------------------------- core ----
    def _proof(self, token: str) -> dict:
        if not self.cfg.app_secret:
            return {}
        return {"appsecret_proof": hmac.new(self.cfg.app_secret.encode(), token.encode(),
                                            hashlib.sha256).hexdigest()}

    def call(self, method: str, path: str, token: str | None = None, retries: int = 2, **params) -> dict:
        token = token or self.cfg.token
        url = f"{GRAPH}/{self.cfg.version}/{path.lstrip('/')}"
        data = {**params, "access_token": token, **self._proof(token)}
        last = None
        for attempt in range(retries + 1):
            try:
                if method == "GET":
                    r = self.s.get(url, params=data, timeout=self.timeout)
                else:
                    r = self.s.post(url, data=data, timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout) as exc:
                last = MetaError(redact(f"réseau: {exc.__class__.__name__}"), transient=True)
                if method != "GET":          # a POST may have been received: caller decides
                    raise last
            else:
                try:
                    body = r.json()
                except ValueError:
                    body = {}
                if r.ok and "error" not in body:
                    return body
                err = body.get("error", {})
                code, sub = err.get("code"), err.get("error_subcode")
                transient = code in TRANSIENT_CODES or r.status_code >= 500 or bool(err.get("is_transient"))
                last = MetaError(redact(f"Meta {r.status_code} code={code} sub={sub}: {err.get('message', r.text[:200])}"),
                                 code=code, subcode=sub, transient=transient, status=r.status_code)
                if not transient:
                    raise last
            if attempt < retries:
                self.sleep(min(5 * 2 ** attempt, 60))
        raise last

    # ------------------------------------------------------- identities ----
    def page_token(self) -> str:
        if not self._page_token:
            body = self.call("GET", self.cfg.page_id, fields="access_token,name")
            tok = body.get("access_token")
            if not tok:
                raise MetaError("jeton de Page introuvable : la Page n'est pas assignée à l'utilisateur système")
            register_secret(tok)
            self._page_token = tok
        return self._page_token

    def page_info(self) -> dict:
        return self.call("GET", self.cfg.page_id, token=self.page_token(),
                         fields="id,name,link,instagram_business_account{id,username}")

    def ig_user_id(self) -> str:
        if self.cfg.ig_user_id:
            return self.cfg.ig_user_id
        ig = self.page_info().get("instagram_business_account") or {}
        if not ig.get("id"):
            raise MetaError("aucun compte Instagram professionnel lié à la Page")
        self.cfg.ig_user_id = ig["id"]
        return ig["id"]

    def permissions(self) -> set[str]:
        body = self.call("GET", "me/permissions")
        return {p["permission"] for p in body.get("data", []) if p.get("status") == "granted"}

    def publishing_limit(self) -> dict:
        body = self.call("GET", f"{self.ig_user_id()}/content_publishing_limit", token=self.page_token(),
                         fields="quota_usage,config")
        return (body.get("data") or [{}])[0]

    # -------------------------------------------------------- instagram ----
    def ig_create_container(self, image_url: str, caption: str = "", story: bool = False) -> str:
        params = {"image_url": image_url}
        if story:
            params["media_type"] = "STORIES"
        elif caption:
            params["caption"] = caption
        body = self.call("POST", f"{self.ig_user_id()}/media", token=self.page_token(), retries=0, **params)
        return body["id"]

    def ig_container_status(self, container_id: str) -> str:
        return self.call("GET", container_id, token=self.page_token(), fields="status_code").get("status_code", "")

    def ig_wait_container(self, container_id: str, poll: int = 5, timeout: int = 180) -> str:
        waited = 0
        while True:
            st = self.ig_container_status(container_id)
            if st in ("FINISHED", "PUBLISHED"):
                return st
            if st in ("ERROR", "EXPIRED"):
                raise MetaError(f"conteneur Instagram {st}")
            if waited >= timeout:
                raise MetaError(f"conteneur Instagram toujours {st or 'inconnu'} après {timeout}s", transient=True)
            self.sleep(poll)
            waited += poll

    def ig_publish(self, container_id: str) -> str:
        try:
            body = self.call("POST", f"{self.ig_user_id()}/media_publish", token=self.page_token(),
                             retries=0, creation_id=container_id)
        except MetaError as exc:
            if exc.transient or exc.status is None:
                raise AmbiguousPublish(str(exc)) from exc
            raise
        return body["id"]

    def ig_permalink(self, media_id: str) -> str:
        try:
            return self.call("GET", media_id, token=self.page_token(), fields="permalink").get("permalink", "")
        except MetaError:
            return ""                      # stories may not expose a permalink

    def ig_recent_media(self, limit: int = 10) -> list[dict]:
        return self.call("GET", f"{self.ig_user_id()}/media", token=self.page_token(),
                         fields="id,caption,timestamp,permalink", limit=limit).get("data", [])

    # --------------------------------------------------------- facebook ----
    def fb_photo(self, image_url: str, caption: str, published: bool = True) -> dict:
        try:
            return self.call("POST", f"{self.cfg.page_id}/photos", token=self.page_token(), retries=0,
                             url=image_url, message=caption, published="true" if published else "false")
        except MetaError as exc:
            if exc.transient or exc.status is None:
                raise AmbiguousPublish(str(exc)) from exc
            raise

    def fb_permalink(self, post_id: str) -> str:
        try:
            return self.call("GET", post_id, token=self.page_token(), fields="permalink_url").get("permalink_url", "")
        except MetaError:
            return ""

    def fb_recent_posts(self, limit: int = 10) -> list[dict]:
        return self.call("GET", f"{self.cfg.page_id}/posts", token=self.page_token(),
                         fields="id,message,created_time,permalink_url", limit=limit).get("data", [])

    def delete(self, object_id: str) -> bool:
        return bool(self.call("POST", object_id, token=self.page_token(), retries=0, method="delete").get("success"))
