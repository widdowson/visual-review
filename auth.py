"""Cloudflare Access authentication for Visual Review.

Why this exists rather than an Access policy alone: Access runs at Cloudflare's
edge, and the edge is not the only way to reach this app. A Cloud Run service
answers any request carrying the mapped Host header, and the address it answers
on — ``ghs.googlehosted.com`` — is the documented CNAME target for every Cloud
Run domain mapping, so it is not a secret. Turning on the orange cloud puts a
login page in front of the *name* and leaves the origin open. Verifying the
signed assertion here closes it: a request that did not traverse Access has no
valid token, whatever hostname or address it arrived on.

That makes this a signature check, not an obscurity measure — which is the whole
point of doing it in the app rather than in DNS.

The token arrives as the ``Cf-Access-Jwt-Assertion`` header on every request
Access forwards, and as the ``CF_Authorization`` cookie in the browser. Both are
accepted; the header is preferred because a cookie is also what a cross-site
request would carry.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import httpx
import jwt

# Cloudflare signs Access tokens with RS256. This list is passed to
# jwt.decode() as the *only* permitted algorithm: PyJWT would otherwise take the
# algorithm from the token's own header, which is the classic JWT forgery.
_ALGORITHMS = ["RS256"]

# How long a fetched JWKS is reused. Cloudflare rotates these keys, and a token
# signed by a key we have not seen triggers an immediate refresh regardless, so
# this only bounds how long a *withdrawn* key stays usable.
_JWKS_TTL = 600.0

# Claims a token must carry to be considered at all. PyJWT verifies exp, iat and
# nbf when present; requiring them means a token that simply omits an expiry is
# rejected rather than treated as eternal.
_REQUIRED_CLAIMS = ["exp", "iat", "aud", "iss", "sub"]

MODE_ACCESS = "cloudflare_access"
MODE_DISABLED = "disabled"

# Cloud Run's liveness probe does not come through Cloudflare, so gating it
# would fail every deployment. Nothing else is exempt: the SPA, the API and the
# static assets are all behind the gate.
EXEMPT_PATHS = frozenset({"/health"})


class AuthError(Exception):
    """A request carried no usable Access token."""


@dataclass
class AccessConfig:
    """Everything needed to verify a token, read once at startup."""

    team_domain: str
    aud: str
    allowed_emails: frozenset[str] = frozenset()

    @property
    def issuer(self) -> str:
        return f"https://{self.team_domain}"

    @property
    def certs_url(self) -> str:
        return f"https://{self.team_domain}/cdn-cgi/access/certs"


@dataclass
class _JwksCache:
    keys: dict[str, object] = field(default_factory=dict)
    fetched_at: float = 0.0


def normalise_team_domain(raw: str) -> str:
    """Accept ``apw``, ``apw.cloudflareaccess.com`` or a full URL.

    The Cloudflare dashboard shows the team name alone in one place and the full
    domain in another, so both spellings get pasted into deployment config. The
    issuer this builds has to match the token's ``iss`` exactly, so normalising
    here beats a 403 that looks like a bad token.
    """
    value = raw.strip()
    if not value:
        return ""
    for prefix in ("https://", "http://"):
        if value.startswith(prefix):
            value = value[len(prefix):]
    value = value.rstrip("/")
    if "." not in value:
        value = f"{value}.cloudflareaccess.com"
    return value


def _parse_emails(raw: str) -> frozenset[str]:
    return frozenset(e.strip().lower() for e in raw.split(",") if e.strip())


def load_config(env: dict[str, str] | None = None) -> AccessConfig | None:
    """Read the auth configuration, or return None when auth is off.

    Fails closed and loudly: the default mode is ``cloudflare_access``, so a
    deployment that forgets to set the team domain refuses to start rather than
    coming up unauthenticated. Turning auth off has to be said out loud with
    VR_AUTH_MODE=disabled, which is what local development and the tests do.
    """
    env = os.environ if env is None else env
    mode = env.get("VR_AUTH_MODE", MODE_ACCESS).strip().lower()

    if mode == MODE_DISABLED:
        return None
    if mode != MODE_ACCESS:
        raise RuntimeError(
            f"VR_AUTH_MODE must be {MODE_ACCESS!r} or {MODE_DISABLED!r}, got {mode!r}"
        )

    team_domain = normalise_team_domain(env.get("CF_ACCESS_TEAM_DOMAIN", ""))
    aud = env.get("CF_ACCESS_AUD", "").strip()
    missing = [name for name, value in
               (("CF_ACCESS_TEAM_DOMAIN", team_domain), ("CF_ACCESS_AUD", aud))
               if not value]
    if missing:
        raise RuntimeError(
            "Cloudflare Access auth is enabled but "
            + " and ".join(missing)
            + " is not set. Set it, or set VR_AUTH_MODE=disabled to run without "
              "authentication (local development only)."
        )

    return AccessConfig(
        team_domain=team_domain,
        aud=aud,
        allowed_emails=_parse_emails(env.get("VR_ALLOWED_EMAILS", "")),
    )


def token_from_request(headers, cookies) -> str:
    """The Access token, preferring the header Cloudflare adds."""
    header = headers.get("cf-access-jwt-assertion", "")
    if header:
        return header.strip()
    return (cookies.get("CF_Authorization") or "").strip()


class AccessVerifier:
    """Verifies Access tokens against Cloudflare's published signing keys."""

    def __init__(self, config: AccessConfig, client_factory=httpx.AsyncClient):
        self.config = config
        self._client_factory = client_factory
        self._jwks = _JwksCache()

    async def _fetch_jwks(self) -> dict[str, object]:
        async with self._client_factory(timeout=10.0) as client:
            resp = await client.get(self.config.certs_url)
            resp.raise_for_status()
            payload = resp.json()

        keys = {}
        for entry in payload.get("keys", []):
            kid = entry.get("kid")
            if not kid:
                continue
            try:
                keys[kid] = jwt.PyJWK.from_dict(entry).key
            except Exception:
                # One unusable entry must not discard the rest of the set.
                continue
        if not keys:
            raise AuthError("Cloudflare published no usable signing keys")
        self._jwks = _JwksCache(keys=keys, fetched_at=time.time())
        return keys

    async def _key_for(self, kid: str):
        fresh = time.time() - self._jwks.fetched_at < _JWKS_TTL
        keys = self._jwks.keys if fresh else await self._fetch_jwks()
        if kid not in keys:
            # A kid we have never seen is the signature of a key rotation, so
            # refetch once before calling it a forgery. Bounded by the fetch
            # itself: a token with a made-up kid costs one request, not a loop.
            keys = await self._fetch_jwks()
        key = keys.get(kid)
        if key is None:
            raise AuthError("token was signed by an unknown key")
        return key

    async def verify(self, token: str) -> dict:
        """Return the token's claims, or raise AuthError."""
        if not token:
            raise AuthError("no Cloudflare Access token on the request")

        try:
            kid = jwt.get_unverified_header(token).get("kid", "")
        except jwt.PyJWTError as exc:
            raise AuthError(f"malformed token: {exc}") from exc
        if not kid:
            raise AuthError("token header carries no key id")

        key = await self._key_for(kid)

        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=_ALGORITHMS,
                audience=self.config.aud,
                issuer=self.config.issuer,
                options={"require": _REQUIRED_CLAIMS},
            )
        except jwt.PyJWTError as exc:
            raise AuthError(f"token rejected: {exc}") from exc

        self._check_allowlist(claims)
        return claims

    def _check_allowlist(self, claims: dict) -> None:
        """Second gate, behind the Access policy rather than instead of it.

        The Access policy is what stops someone signing in at all. This is the
        belt to that pair of braces: if the policy is ever widened by accident,
        the app still only serves the addresses named in its own configuration.
        Empty means "whoever the Access policy lets through", which is the
        sensible default once the policy is the allowlist.
        """
        if not self.config.allowed_emails:
            return
        email = (claims.get("email") or "").strip().lower()
        if not email:
            raise AuthError("token carries no email to check against the allowlist")
        if email not in self.config.allowed_emails:
            raise AuthError(f"{email} is not on this deployment's allowlist")


class AccessMiddleware:
    """Pure-ASGI gate. Deliberately not a BaseHTTPMiddleware subclass.

    BaseHTTPMiddleware replaces the ASGI receive channel with one of its own,
    and Request.is_disconnected() detects a gone client by polling receive for
    an http.disconnect message. Behind BaseHTTPMiddleware that message is
    swallowed, is_disconnected() never returns True, and pr_image goes on
    fetching megabytes from GitHub for a browser that navigated away — the
    exact optimisation //:test_app's TestPrImageClientDisconnect exists to pin.
    It caught this: the first version of this gate was an @app.middleware("http")
    and turned all eight of those tests red.

    A raw ASGI middleware passes receive through untouched, so the disconnect
    reaches the view.
    """

    def __init__(self, app, get_verifier=lambda: None,
                 exempt_paths=EXEMPT_PATHS, logger=None):
        self.app = app
        # Resolved per request rather than captured here. Starlette builds the
        # middleware stack once at startup, so a verifier passed in by value
        # would freeze whatever existed at import - which is None under the
        # suite, leaving every "gate armed" test silently ungated.
        self.get_verifier = get_verifier
        self.exempt_paths = exempt_paths
        self._logger = logger

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        state = scope.setdefault("state", {})
        state["access_claims"] = None

        verifier = self.get_verifier()
        if verifier is None or scope.get("path") in self.exempt_paths:
            return await self.app(scope, receive, send)

        # Imported here rather than at module scope so that auth.py stays
        # importable (and unit-testable) without pulling in Starlette.
        from starlette.requests import Request
        from starlette.responses import JSONResponse

        request = Request(scope, receive)
        path = scope.get("path", "")

        try:
            claims = await verifier.verify(
                token_from_request(request.headers, request.cookies)
            )
        except AuthError as exc:
            # Logged without the token: a rejected token is still a live bearer
            # credential until it expires, so it does not belong in a log line.
            self._log("warning", "Access denied for %s: %s", path, exc)
            response = JSONResponse(
                status_code=403,
                content={
                    "error": "Not authenticated",
                    "detail": str(exc),
                    "hint": "Open this app through its Cloudflare Access URL.",
                },
            )
            return await response(scope, receive, send)
        except Exception as exc:
            # A JWKS fetch that fails must not become a way in. 503 rather than
            # 403 because the caller may be perfectly entitled and the fault is
            # ours.
            self._log("error", "Access verification failed for %s: %s", path, exc)
            response = JSONResponse(
                status_code=503,
                content={"error": "Authentication is temporarily unavailable"},
            )
            return await response(scope, receive, send)

        state["access_claims"] = claims
        return await self.app(scope, receive, send)

    def _log(self, level: str, *args) -> None:
        if self._logger is not None:
            getattr(self._logger, level)(*args)
