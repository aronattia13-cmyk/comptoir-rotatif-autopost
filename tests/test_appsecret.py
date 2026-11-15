"""appsecret_proof: signature correctness, token hygiene, error classification."""
import hashlib
import hmac

import pytest

from comptoir import meta, publisher
from comptoir.meta import MetaClient, MetaConfig, MetaConfigError, MetaError

SECRET = "0123456789abcdef0123456789abcdef"          # fake, 32 hex
SYS = "EAAsystemTOKENfake1234567890"
PAGE = "EAApageTOKENfake0987654321"


class R:
    def __init__(self, status, body):
        self.status_code, self._b, self.ok = status, body, 200 <= status < 300
        self.text = str(body)

    def json(self):
        return self._b


class Session:
    """Minimal Graph API double that VERIFIES appsecret_proof like Meta does."""
    def __init__(self, server_secret=SECRET, require_proof=True, token_app="111", ):
        self.server_secret, self.require = server_secret, require_proof
        self.token_app, self.calls = token_app, []

    def _check(self, data):
        tok = data["access_token"]
        if "|" in tok:                                        # app access token
            app, sec = tok.split("|", 1)
            return None if sec == self.server_secret else R(400, {"error": {"code": 190, "message": "Invalid OAuth access token"}})
        proof = data.get("appsecret_proof")
        if proof is None:
            if self.require:
                return R(400, {"error": {"code": 100, "message": "API calls from the server require an appsecret_proof argument"}})
            return None
        good = hmac.new(self.server_secret.encode(), tok.encode(), hashlib.sha256).hexdigest()
        if proof != good:
            return R(400, {"error": {"code": 100, "message": "Invalid appsecret_proof provided in the API argument"}})
        return None

    def _route(self, method, url, data):
        path = url.split("/v25.0/", 1)[1]
        self.calls.append((method, path, dict(data)))
        err = self._check(data)
        if err:
            return err
        if path == "debug_token":
            return R(200, {"data": {"is_valid": True, "app_id": self.token_app, "type": "SYSTEM_USER", "expires_at": 0}})
        if path == "me":
            return R(200, {"id": "1", "name": "comptoir-github"})
        if path == "me/permissions":
            return R(200, {"data": [{"permission": p, "status": "granted"} for p in meta.REQUIRED_SCOPES | meta.PORTFOLIO_SCOPES]})
        if path == "123":
            return R(200, {"access_token": PAGE, "name": "LCDR", "instagram_business_account": {"id": "9"}})
        if path.endswith("content_publishing_limit"):
            return R(200, {"data": [{"quota_usage": 0}]})
        raise AssertionError(path)

    def get(self, url, params=None, timeout=None):
        return self._route("GET", url, params)

    def post(self, url, data=None, timeout=None):
        return self._route("POST", url, data)


def client(secret=SECRET, app_id="111", **kw):
    cfg = MetaConfig(token=SYS, page_id="123", app_secret=secret, app_id=app_id, notes=[])
    return MetaClient(cfg, session=Session(**kw), sleep=lambda s: None)


# --------------------------------------------------------------- signature ----
def test_hmac_known_answer():
    assert meta.self_test_signature()
    assert meta.appsecret_proof("key", "The quick brown fox jumps over the lazy dog") == \
        "f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8"


def test_each_request_signs_the_exact_token_it_sends():
    c = client()
    c.call("GET", "me")
    c.page_token()
    c.publishing_limit()
    for _, _, data in c.s.calls:
        assert data["appsecret_proof"] == meta.appsecret_proof(SECRET, data["access_token"])
    tokens = {d["access_token"] for _, _, d in c.s.calls}
    assert tokens == {SYS, PAGE}                     # page token signed separately


def test_correct_secret_accepted_by_strict_server():
    assert client().call("GET", "me")["name"] == "comptoir-github"


# ----------------------------------------------------------------- hygiene ----
@pytest.mark.parametrize("raw", [SYS + "\n", " " + SYS, SYS + "\r\n", f'"{SYS}"', f"Bearer {SYS}"])
def test_copy_paste_accidents_are_cleaned_and_reported(raw):
    notes = []
    assert meta.clean_secret("META_SYSTEM_USER_TOKEN", raw, notes) == SYS
    assert notes and all(SYS not in n for n in notes)


def test_inner_whitespace_is_a_config_error():
    with pytest.raises(MetaConfigError):
        meta.clean_secret("META_APP_SECRET", "0123 4567", [])


def test_from_env_strips_trailing_newline_so_signature_matches(monkeypatch):
    monkeypatch.setenv("META_SYSTEM_USER_TOKEN", SYS + "\n")
    monkeypatch.setenv("META_APP_SECRET", SECRET + "\n")
    monkeypatch.setenv("META_PAGE_ID", " 123 ")
    cfg = MetaConfig.from_env()
    assert cfg.token == SYS and cfg.app_secret == SECRET and cfg.page_id == "123"
    c = MetaClient(cfg, session=Session(), sleep=lambda s: None)
    assert c.call("GET", "me")["id"] == "1"           # would fail with the old code
    assert any("META_SYSTEM_USER_TOKEN" in n for n in cfg.notes)


def test_non_numeric_page_id(monkeypatch):
    monkeypatch.setenv("META_SYSTEM_USER_TOKEN", SYS)
    monkeypatch.setenv("META_PAGE_ID", "LeComptoir")
    with pytest.raises(MetaConfigError):
        MetaConfig.from_env()


# ---------------------------------------------------------- classification ----
def test_invalid_proof_is_classified_as_configuration():
    c = client(secret="ffffffffffffffffffffffffffffffff")     # secret of another app
    with pytest.raises(MetaConfigError, match="appsecret_proof"):
        c.call("GET", "me")


def rows_by_name(rows):
    return {n: (cat, d) for n, cat, d in rows}


def test_diagnose_wrong_secret_with_app_id_points_to_configuration():
    c = client(secret="ffffffffffffffffffffffffffffffff")
    rows = rows_by_name(publisher.diagnose(c, None))
    assert rows["Calcul de la signature appsecret_proof (auto-test du code)"][0] == "OK"
    cat, detail = rows["Correspondance jeton ↔ application ↔ clé secrète"]
    assert cat == "CONFIGURATION" and "ne correspond pas" in detail
    assert rows["Jeton système valide"][0] == "CONFIGURATION"


def test_diagnose_probe_without_app_id_unsigned_ok():
    c = client(secret="ffffffffffffffffffffffffffffffff", app_id=None, require_proof=False)
    rows = rows_by_name(publisher.diagnose(c, None))
    cat, detail = rows["Jeton système valide"]
    assert cat == "CONFIGURATION" and "n'appartient pas" in detail


def test_diagnose_probe_with_require_app_secret():
    c = client(secret="ffffffffffffffffffffffffffffffff", app_id=None, require_proof=True)
    cat, detail = rows_by_name(publisher.diagnose(c, None))["Jeton système valide"]
    assert cat == "CONFIGURATION" and "Exiger la clé secrète" in detail


def test_diagnose_token_from_another_app():
    c = client(token_app="999")
    cat, detail = rows_by_name(publisher.diagnose(c, None))["Correspondance jeton ↔ application ↔ clé secrète"]
    assert cat == "CONFIGURATION" and "AUTRE application" in detail


def test_diagnose_all_good():
    rows = publisher.diagnose(client(), None)
    bad = [r for r in rows if r[1] != "OK" and "Image publique" not in r[0]]
    assert not bad, bad


def test_badly_formatted_secret_flagged():
    c = client(secret="not-a-meta-secret")
    cat, detail = rows_by_name(publisher.diagnose(c, None))["Format des secrets (sans afficher les valeurs)"]
    assert cat == "CONFIGURATION" and "32 caractères" in detail


def test_programming_error_is_classified_as_code(monkeypatch):
    c = client()
    monkeypatch.setattr(c, "page_token", lambda: (_ for _ in ()).throw(TypeError("boom")))
    cat, detail = rows_by_name(publisher.diagnose(c, None))["Accès à la Page (jeton de Page dérivé, signé séparément)"]
    assert cat == "CODE" and "TypeError" in detail


def test_broken_hmac_is_reported_as_code(monkeypatch):
    monkeypatch.setattr(meta, "self_test_signature", lambda: False)
    rows = publisher.diagnose(client(), None)
    assert rows[1][1] == "CODE" and len(rows) == 2            # stops before calling Meta


def test_secrets_never_appear_in_diagnostic_output():
    meta.register_secret(SYS)
    meta.register_secret(SECRET)
    for s in ("ffffffffffffffffffffffffffffffff",):
        meta.register_secret(s)
    for c in (client(), client(secret="ffffffffffffffffffffffffffffffff"), client(token_app="999")):
        for _, _, detail in publisher.diagnose(c, None):
            for secret in (SYS, PAGE, SECRET, "ffffffffffffffffffffffffffffffff"):
                assert secret not in detail
