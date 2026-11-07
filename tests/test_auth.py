"""Tests for the Cloudflare Access gate.

The gate is the only thing standing between this deployment's `repo`-scoped
GitHub token and the internet (widdowson/visual-review#41), so these lean hard
on the rejection cases: a test suite that only proves a good token is accepted
would stay green against a gate that accepts everything.
"""

import json
import sys
import time
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import auth  # noqa: E402
from auth import (  # noqa: E402
    AccessConfig,
    AccessVerifier,
    AuthError,
    load_config,
    normalise_team_domain,
    token_from_request,
)

TEAM = "apw.cloudflareaccess.com"
AUD = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
ISSUER = f"https://{TEAM}"


# -- Signing fixtures ----------------------------------------------------------

def _rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class Signer:
    """A stand-in for Cloudflare's signing key, with the JWKS it publishes."""

    def __init__(self, kid: str = "kid-1"):
        self.kid = kid
        self.key = _rsa_key()

    def jwk(self) -> dict:
        entry = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        entry.update({"kid": self.kid, "alg": "RS256", "use": "sig"})
        return entry

    def token(self, **overrides) -> str:
        now = int(time.time())
        claims = {
            "aud": [AUD],
            "email": "apw@apw.photos",
            "exp": now + 3600,
            "iat": now,
            "iss": ISSUER,
            "sub": "user-1",
            "type": "app",
        }
        claims.update(overrides)
        for key, value in list(claims.items()):
            if value is None:
                del claims[key]
        return jwt.encode(claims, self.key, algorithm="RS256",
                          headers={"kid": self.kid})


class FakeJwksEndpoint:
    """An httpx.AsyncClient stand-in serving a JWKS, counting its fetches."""

    def __init__(self, *signers, fail: Exception | None = None):
        self.signers = list(signers)
        self.fail = fail
        self.extra: list[dict] = []
        self.fetches = 0

    def factory(self, *args, **kwargs):
        endpoint = self

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url):
                endpoint.fetches += 1
                if endpoint.fail:
                    raise endpoint.fail
                return _Response({"keys": [s.jwk() for s in endpoint.signers]
                                         + endpoint.extra})

        return _Client()


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def verifier(*signers, config: AccessConfig | None = None, endpoint=None):
    endpoint = endpoint or FakeJwksEndpoint(*signers)
    cfg = config or AccessConfig(team_domain=TEAM, aud=AUD)
    v = AccessVerifier(cfg, client_factory=endpoint.factory)
    v.endpoint = endpoint
    return v


# -- Configuration -------------------------------------------------------------

class TestNormaliseTeamDomain:
    @pytest.mark.parametrize("raw", [
        "apw",
        "apw.cloudflareaccess.com",
        "https://apw.cloudflareaccess.com",
        "https://apw.cloudflareaccess.com/",
        "  apw.cloudflareaccess.com  ",
    ])
    def test_every_spelling_lands_on_the_same_domain(self, raw):
        assert normalise_team_domain(raw) == TEAM

    def test_empty_stays_empty(self):
        assert normalise_team_domain("   ") == ""


class TestLoadConfig:
    def test_disabled_returns_none(self):
        assert load_config({"VR_AUTH_MODE": "disabled"}) is None

    def test_default_mode_is_enabled(self):
        """The default must be on. A deployment that sets nothing has to fail,
        not serve."""
        with pytest.raises(RuntimeError) as exc:
            load_config({})
        assert "CF_ACCESS_TEAM_DOMAIN" in str(exc.value)

    def test_missing_aud_is_fatal(self):
        with pytest.raises(RuntimeError) as exc:
            load_config({"CF_ACCESS_TEAM_DOMAIN": "apw"})
        assert "CF_ACCESS_AUD" in str(exc.value)

    def test_unknown_mode_is_fatal(self):
        with pytest.raises(RuntimeError):
            load_config({"VR_AUTH_MODE": "off"})

    def test_full_config(self):
        cfg = load_config({
            "CF_ACCESS_TEAM_DOMAIN": "apw",
            "CF_ACCESS_AUD": AUD,
            "VR_ALLOWED_EMAILS": "APW@apw.photos, widdowson@gmail.com",
        })
        assert cfg.team_domain == TEAM
        assert cfg.issuer == ISSUER
        assert cfg.certs_url == f"{ISSUER}/cdn-cgi/access/certs"
        assert cfg.allowed_emails == frozenset(
            {"apw@apw.photos", "widdowson@gmail.com"})


class TestTokenFromRequest:
    def test_header_wins_over_cookie(self):
        assert token_from_request(
            {"cf-access-jwt-assertion": "from-header"},
            {"CF_Authorization": "from-cookie"},
        ) == "from-header"

    def test_cookie_is_the_fallback(self):
        assert token_from_request({}, {"CF_Authorization": "from-cookie"}) \
            == "from-cookie"

    def test_neither_is_empty(self):
        assert token_from_request({}, {}) == ""


# -- Verification --------------------------------------------------------------

@pytest.mark.asyncio
class TestVerify:
    async def test_a_good_token_is_accepted(self):
        s = Signer()
        claims = await verifier(s).verify(s.token())
        assert claims["email"] == "apw@apw.photos"

    async def test_no_token_is_rejected(self):
        with pytest.raises(AuthError):
            await verifier(Signer()).verify("")

    async def test_garbage_is_rejected(self):
        with pytest.raises(AuthError):
            await verifier(Signer()).verify("not-a-jwt")

    async def test_expired_is_rejected(self):
        s = Signer()
        with pytest.raises(AuthError):
            await verifier(s).verify(s.token(exp=int(time.time()) - 60))

    async def test_wrong_audience_is_rejected(self):
        """The AUD tag is per-Access-application. Without this check a token
        from any other Access app on the same team would open this one."""
        s = Signer()
        with pytest.raises(AuthError):
            await verifier(s).verify(s.token(aud=["some-other-application"]))

    async def test_wrong_issuer_is_rejected(self):
        s = Signer()
        with pytest.raises(AuthError):
            await verifier(s).verify(s.token(iss="https://evil.cloudflareaccess.com"))

    async def test_a_token_signed_by_a_stranger_is_rejected(self):
        """The signature is the whole mechanism: same claims, same kid, key we
        never published."""
        real, impostor = Signer(), Signer()
        with pytest.raises(AuthError):
            await verifier(real).verify(impostor.token())

    async def test_the_permitted_algorithm_list_is_exactly_rs256(self):
        """A structural assertion, and deliberately so.

        The attack the pin exists for is algorithm confusion: the signing key is
        public by construction (Cloudflare publishes it at
        /cdn-cgi/access/certs), so an attacker can use those bytes as an HMAC
        secret, sign any claims with HS256, and a verifier that reads the
        algorithm out of the token's own header will check the HMAC against the
        very bytes the attacker used.

        There is no behavioural test for it here, because with PyJWT 2.14 that
        forgery cannot be built or verified at all: HMACAlgorithm.prepare_key
        rejects a PEM or DER key outright, on encode and on decode alike, so a
        "forged HS256 token is rejected" assertion stays green even with HS256
        added to the list below. Both were tried. Rather than ship an assertion
        that cannot fail, this pins the constant: widening it turns this red,
        which is the whole property worth defending, and it does not pretend to
        be evidence about runtime behaviour.
        """
        assert auth._ALGORITHMS == ["RS256"]

    async def test_a_token_with_no_expiry_is_rejected(self):
        s = Signer()
        with pytest.raises(AuthError):
            await verifier(s).verify(s.token(exp=None))

    async def test_a_token_with_no_kid_is_rejected(self):
        s = Signer()
        token = jwt.encode({"aud": [AUD], "iss": ISSUER, "sub": "x",
                            "exp": int(time.time()) + 60, "iat": int(time.time())},
                           s.key, algorithm="RS256")
        with pytest.raises(AuthError):
            await verifier(s).verify(token)


@pytest.mark.asyncio
class TestJwks:
    async def test_the_key_set_is_cached_across_requests(self):
        s = Signer()
        v = verifier(s)
        await v.verify(s.token())
        await v.verify(s.token())
        assert v.endpoint.fetches == 1

    async def test_an_unknown_kid_refetches_once(self):
        """Key rotation: Cloudflare signs with a key we have not seen. One
        refetch, and the token verifies."""
        old, new = Signer("kid-old"), Signer("kid-new")
        endpoint = FakeJwksEndpoint(old)
        v = verifier(endpoint=endpoint)
        await v.verify(old.token())
        assert v.endpoint.fetches == 1

        endpoint.signers = [new]
        await v.verify(new.token())
        assert v.endpoint.fetches == 2

    async def test_a_kid_that_never_appears_is_rejected(self):
        real = Signer("kid-real")
        impostor = Signer("kid-made-up")
        with pytest.raises(AuthError):
            await verifier(real).verify(impostor.token())

    async def test_the_library_can_read_an_rsa_key(self):
        """PyJWT here can read an RSA JWK, i.e. it has its crypto backend.

        This cannot fail alone: without cryptography the module's own import
        fails first. What the image ships is //:test_image_contents's job."""
        assert jwt.algorithms.has_crypto
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(
            Signer().key.public_key()))
        assert jwt.PyJWK.from_dict({**jwk, "kid": "k", "alg": "RS256"}).key

    async def test_a_key_set_nothing_can_parse_says_why(self):
        endpoint = FakeJwksEndpoint()
        endpoint.extra = [{"kid": "bad", "kty": "RSA", "alg": "RS256",
                           "n": "!!", "e": "AQAB"}]
        v = verifier(endpoint=endpoint)
        with pytest.raises(AuthError, match=r"no usable signing keys \(.+\)"):
            await v._fetch_jwks()

    async def test_an_empty_key_set_is_an_error_not_an_open_door(self):
        s = Signer()
        v = verifier(endpoint=FakeJwksEndpoint())
        with pytest.raises(AuthError):
            await v.verify(s.token())


@pytest.mark.asyncio
class TestAllowlist:
    async def _verify_with_allowlist(self, emails, email):
        s = Signer()
        cfg = AccessConfig(team_domain=TEAM, aud=AUD,
                           allowed_emails=frozenset(emails))
        return await verifier(s, config=cfg).verify(s.token(email=email))

    async def test_an_allowed_address_passes(self):
        claims = await self._verify_with_allowlist(
            {"apw@apw.photos"}, "apw@apw.photos")
        assert claims["email"] == "apw@apw.photos"

    async def test_case_is_not_a_way_round_it(self):
        claims = await self._verify_with_allowlist(
            {"apw@apw.photos"}, "APW@APW.PHOTOS")
        assert claims["email"] == "APW@APW.PHOTOS"

    async def test_an_address_not_on_the_list_is_rejected(self):
        with pytest.raises(AuthError):
            await self._verify_with_allowlist(
                {"apw@apw.photos"}, "someone@example.com")

    async def test_an_empty_list_defers_to_the_access_policy(self):
        claims = await self._verify_with_allowlist(set(), "anyone@example.com")
        assert claims["email"] == "anyone@example.com"


# -- The middleware, end to end ------------------------------------------------

@pytest.fixture
def gated(monkeypatch):
    """The real app with the gate armed, and the signer its tokens come from."""
    import app as app_module

    signer = Signer()
    endpoint = FakeJwksEndpoint(signer)
    v = AccessVerifier(AccessConfig(team_domain=TEAM, aud=AUD),
                       client_factory=endpoint.factory)
    monkeypatch.setattr(app_module, "_access_verifier", v)
    return app_module.app, signer, endpoint


async def _get(application, path, **kwargs):
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        return await ac.get(path, **kwargs)


@pytest.mark.asyncio
class TestGate:
    async def test_the_spa_is_refused_without_a_token(self, gated):
        application, _, _ = gated
        resp = await _get(application, "/widdowson/apwphotos-appv2/pr/25")
        assert resp.status_code == 403

    async def test_the_api_is_refused_without_a_token(self, gated):
        application, _, _ = gated
        resp = await _get(application, "/api/widdowson/apwphotos-appv2/pr/25/images")
        assert resp.status_code == 403

    async def test_the_repo_page_is_refused_too(self, gated):
        """Added with the repo browser. Every route but /health is gated by
        construction, so this is here to notice if that ever stops being true
        for a page that lists someone's pull requests."""
        application, _, _ = gated
        assert (await _get(application, "/widdowson/apwphotos-appv2")).status_code == 403

    async def test_the_pulls_api_is_refused_too(self, gated):
        application, _, _ = gated
        resp = await _get(application, "/api/widdowson/apwphotos-appv2/pulls")
        assert resp.status_code == 403

    async def test_static_assets_are_refused_too(self, gated):
        application, _, _ = gated
        resp = await _get(application, "/static/favicon.svg")
        assert resp.status_code == 403

    async def test_health_stays_open(self, gated):
        """Cloud Run's liveness probe does not come through Cloudflare. If this
        were gated, every deployment would fail its health check."""
        application, _, _ = gated
        resp = await _get(application, "/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    async def test_a_valid_header_token_gets_through(self, gated):
        application, signer, _ = gated
        resp = await _get(application, "/widdowson/apwphotos-appv2/pr/25",
                          headers={"Cf-Access-Jwt-Assertion": signer.token()})
        assert resp.status_code == 200

    async def test_a_valid_cookie_gets_through(self, gated):
        application, signer, _ = gated
        resp = await _get(application, "/widdowson/apwphotos-appv2/pr/25",
                          cookies={"CF_Authorization": signer.token()})
        assert resp.status_code == 200

    async def test_a_rejection_does_not_echo_the_token(self, gated):
        """A rejected token is still a live bearer credential until it expires."""
        application, signer, _ = gated
        token = signer.token(aud=["wrong"])
        resp = await _get(application, "/widdowson/apwphotos-appv2/pr/25",
                          headers={"Cf-Access-Jwt-Assertion": token})
        assert resp.status_code == 403
        assert token not in resp.text

    async def test_a_jwks_outage_is_503_not_403(self, gated, monkeypatch):
        """Ours to fix, not the caller's — and 503 keeps it out of the bucket a
        reviewer would read as 'you are not allowed in'."""
        import app as app_module
        signer = Signer()
        endpoint = FakeJwksEndpoint(signer, fail=RuntimeError("network down"))
        monkeypatch.setattr(
            app_module, "_access_verifier",
            AccessVerifier(AccessConfig(team_domain=TEAM, aud=AUD),
                           client_factory=endpoint.factory))
        resp = await _get(app_module.app, "/widdowson/apwphotos-appv2/pr/25",
                          headers={"Cf-Access-Jwt-Assertion": signer.token()})
        assert resp.status_code == 503


@pytest.mark.asyncio
class TestWhoami:
    async def test_reports_the_signed_in_address(self, gated):
        application, signer, _ = gated
        resp = await _get(application, "/api/me",
                          headers={"Cf-Access-Jwt-Assertion": signer.token()})
        assert resp.status_code == 200
        assert resp.json() == {"authenticated": True, "email": "apw@apw.photos"}

    async def test_is_gated_like_everything_else(self, gated):
        application, _, _ = gated
        assert (await _get(application, "/api/me")).status_code == 403

    async def test_reports_unauthenticated_when_the_gate_is_off(self):
        """Local development: the SPA asks, gets a plain no, and draws no chip
        rather than treating the 403 as a bug."""
        import app as app_module
        assert app_module._access_verifier is None, \
            "the suite must run with VR_AUTH_MODE=disabled"
        resp = await _get(app_module.app, "/api/me")
        assert resp.status_code == 200
        assert resp.json() == {"authenticated": False}


# -- LGTM ----------------------------------------------------------------------
#
# The `lgtm` label merges a PR, so these are mostly refusals: a suite that only
# proved Andrew can apply it would stay green against an endpoint anyone could
# use. Every refusal also asserts GitHub was never asked to add a label.

ANDREW = "apw@apw.photos"
PR_PATH = "/api/widdowson/apwphotos-appv2/pr/25/lgtm"
INTENT = {"X-VR-Intent": "lgtm"}


class FakeGitHub:
    """Stands in for httpx.AsyncClient in app.py, recording every request."""

    def __init__(self, *, label=200, pr_state="open", labels=(), head="abc123",
                 add_status=200):
        self.label = label
        self.pr = {
            "state": pr_state,
            "draft": False,
            "merged_at": None,
            "labels": [{"name": n} for n in labels],
            "head": {"sha": head},
        }
        self.add_status = add_status
        self.calls: list[tuple[str, str, object]] = []

    def factory(self, *args, **kwargs):
        gh = self

        class _Resp:
            def __init__(self, status, payload=None):
                self.status_code = status
                self._payload = payload
                self.text = json.dumps(payload)

            def json(self):
                return self._payload

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url, **kw):
                gh.calls.append(("GET", url, None))
                if url.endswith("/labels/lgtm"):
                    return _Resp(gh.label, {"name": "lgtm"})
                if "/pulls/" in url:
                    return _Resp(200, gh.pr)
                return _Resp(404, {})

            async def post(self, url, json=None, **kw):
                gh.calls.append(("POST", url, json))
                return _Resp(gh.add_status, [{"name": "lgtm"}])

        return _Client()

    @property
    def label_posts(self):
        return [c for c in self.calls if c[0] == "POST"]


@pytest.fixture
def lgtm(gated, monkeypatch):
    """The gated app with LGTM enabled for Andrew, and a fake GitHub."""
    import app as app_module
    application, signer, _ = gated
    gh = FakeGitHub()
    monkeypatch.setattr(app_module, "LGTM_EMAILS", frozenset({ANDREW}))
    monkeypatch.setattr(app_module, "GITHUB_TOKEN", "fake-token")
    monkeypatch.setattr(app_module.httpx, "AsyncClient", gh.factory)
    app_module._cache.clear()
    return application, signer, gh


async def _post(application, path, **kwargs):
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        return await ac.post(path, **kwargs)


def _service_token(signer):
    """What Access issues a service token: a common_name and no email."""
    return signer.token(email=None, common_name="0123abcd.access", sub="")


@pytest.mark.asyncio
class TestLgtmApply:
    async def test_andrew_applies_the_label(self, lgtm):
        application, signer, gh = lgtm
        resp = await _post(application, PR_PATH, headers={
            **INTENT, "Cf-Access-Jwt-Assertion": signer.token()},
            json={"head_sha": "abc123"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["ok"] is True and resp.json()["applied"] is True
        assert gh.label_posts == [(
            "POST",
            "https://api.github.com/repos/widdowson/apwphotos-appv2/issues/25/labels",
            {"labels": ["lgtm"]},
        )]

    async def test_the_cookie_works_as_well_as_the_header(self, lgtm):
        """The SPA's own fetch carries the cookie; Access adds the header."""
        application, signer, gh = lgtm
        resp = await _post(application, PR_PATH, headers=INTENT,
                           cookies={"CF_Authorization": signer.token()})
        assert resp.status_code == 200
        assert len(gh.label_posts) == 1

    async def test_case_in_the_address_does_not_matter(self, lgtm):
        application, signer, gh = lgtm
        resp = await _post(application, PR_PATH, headers={
            **INTENT, "Cf-Access-Jwt-Assertion": signer.token(email="APW@apw.photos")})
        assert resp.status_code == 200
        assert len(gh.label_posts) == 1

    async def test_a_service_token_is_refused(self, lgtm):
        """How an agent reaches an Access app. No email, so no LGTM."""
        application, signer, gh = lgtm
        resp = await _post(application, PR_PATH, headers={
            **INTENT, "Cf-Access-Jwt-Assertion": _service_token(signer)})
        assert resp.status_code == 403
        assert "service token" in resp.json()["error"]
        assert gh.calls == []

    async def test_someone_else_signed_in_is_refused(self, lgtm):
        """Getting into VR to look at a PR is not leave to merge it."""
        application, signer, gh = lgtm
        resp = await _post(application, PR_PATH, headers={
            **INTENT, "Cf-Access-Jwt-Assertion": signer.token(email="reviewer@example.com")})
        assert resp.status_code == 403
        assert gh.calls == []

    async def test_no_token_never_reaches_the_endpoint(self, lgtm):
        application, _, gh = lgtm
        resp = await _post(application, PR_PATH, headers=INTENT)
        assert resp.status_code == 403
        assert gh.calls == []

    async def test_an_unset_list_turns_it_off_for_everyone(self, lgtm, monkeypatch):
        import app as app_module
        application, signer, gh = lgtm
        monkeypatch.setattr(app_module, "LGTM_EMAILS", frozenset())
        resp = await _post(application, PR_PATH, headers={
            **INTENT, "Cf-Access-Jwt-Assertion": signer.token()})
        assert resp.status_code == 403
        assert gh.calls == []

    async def test_the_gate_off_means_no_not_anyone(self, monkeypatch):
        """Local development has no identity, so nobody may apply it."""
        import app as app_module
        assert app_module._access_verifier is None
        gh = FakeGitHub()
        monkeypatch.setattr(app_module, "LGTM_EMAILS", frozenset({ANDREW}))
        monkeypatch.setattr(app_module, "GITHUB_TOKEN", "fake-token")
        monkeypatch.setattr(app_module.httpx, "AsyncClient", gh.factory)
        resp = await _post(app_module.app, PR_PATH, headers=INTENT)
        assert resp.status_code == 403
        assert gh.calls == []

    async def test_without_the_intent_header_it_is_refused(self, lgtm):
        """What another site's form post would look like, cookie and all."""
        application, signer, gh = lgtm
        resp = await _post(application, PR_PATH,
                           cookies={"CF_Authorization": signer.token()})
        assert resp.status_code == 400
        assert gh.calls == []

    async def test_another_site_cannot_get_a_preflight_for_the_header(self, lgtm):
        """The intent header is only worth something while no other origin
        may send it, which is the CORS layer's GET-only list."""
        application, _, _ = lgtm
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.options(PR_PATH, headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "x-vr-intent",
            })
        assert "POST" not in resp.headers.get("access-control-allow-methods", "")
        assert resp.status_code >= 400

    async def test_new_commits_since_the_page_loaded_are_not_approved(self, lgtm):
        application, signer, gh = lgtm
        resp = await _post(application, PR_PATH, headers={
            **INTENT, "Cf-Access-Jwt-Assertion": signer.token()},
            json={"head_sha": "older"})
        assert resp.status_code == 409
        assert gh.label_posts == []

    async def test_a_closed_pr_is_refused(self, lgtm):
        application, signer, gh = lgtm
        gh.pr["state"] = "closed"
        resp = await _post(application, PR_PATH, headers={
            **INTENT, "Cf-Access-Jwt-Assertion": signer.token()})
        assert resp.status_code == 409
        assert gh.label_posts == []

    async def test_a_repo_with_no_lgtm_label_is_left_alone(self, lgtm):
        """GitHub would create the label; a stray one would merge nothing."""
        application, signer, gh = lgtm
        gh.label = 404
        resp = await _post(application, PR_PATH, headers={
            **INTENT, "Cf-Access-Jwt-Assertion": signer.token()})
        assert resp.status_code == 404
        assert gh.label_posts == []

    async def test_already_applied_is_ok_and_adds_nothing(self, lgtm):
        application, signer, gh = lgtm
        gh.pr["labels"] = [{"name": "lgtm"}]
        resp = await _post(application, PR_PATH, headers={
            **INTENT, "Cf-Access-Jwt-Assertion": signer.token()})
        assert resp.status_code == 200 and resp.json()["applied"] is True
        assert gh.label_posts == []

    async def test_a_github_refusal_is_reported_not_swallowed(self, lgtm):
        application, signer, gh = lgtm
        gh.add_status = 403
        resp = await _post(application, PR_PATH, headers={
            **INTENT, "Cf-Access-Jwt-Assertion": signer.token()})
        assert resp.status_code == 502
        assert "403" in resp.json()["error"]


@pytest.mark.asyncio
class TestLgtmStatus:
    async def test_andrew_is_offered_the_button(self, lgtm):
        application, signer, _ = lgtm
        resp = await _get(application, PR_PATH,
                          headers={"Cf-Access-Jwt-Assertion": signer.token()})
        body = resp.json()
        assert body["can_apply"] is True
        assert body["applied"] is False
        assert body["head_sha"] == "abc123"

    async def test_a_service_token_is_not_offered_it(self, lgtm):
        application, signer, gh = lgtm
        resp = await _get(application, PR_PATH,
                          headers={"Cf-Access-Jwt-Assertion": _service_token(signer)})
        assert resp.json()["can_apply"] is False
        assert gh.calls == []

    async def test_someone_else_is_not_offered_it(self, lgtm):
        application, signer, gh = lgtm
        resp = await _get(application, PR_PATH, headers={
            "Cf-Access-Jwt-Assertion": signer.token(email="reviewer@example.com")})
        assert resp.json()["can_apply"] is False
        assert gh.calls == []

    async def test_a_closed_pr_is_not_offered_it(self, lgtm):
        application, signer, gh = lgtm
        gh.pr["state"] = "closed"
        resp = await _get(application, PR_PATH,
                          headers={"Cf-Access-Jwt-Assertion": signer.token()})
        assert resp.json()["can_apply"] is False
