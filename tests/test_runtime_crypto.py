"""The deployed dependency set must be able to verify an RS256 token.

This exists because every other test in this repository passed while the
deployed image could not verify a single token (#41 follow-up). PyJWT without
``cryptography`` imports cleanly, registers no RSA algorithm, and raises
``MissingCryptographyError`` from ``PyJWK.from_dict`` on every key Cloudflare
publishes -- which ``_fetch_jwks`` turned into "no usable signing keys",
blaming Cloudflare for a package that was never installed.

``//:test_auth`` could not see it: it declares ``cryptography`` itself, for the
RSA ``Signer`` fixture it signs test tokens with. So the gap was precisely
between the two dependency sets, and no assertion written inside the one that
had the package could reach it.

**This target therefore declares only ``:app_lib``.** It is the production
closure and nothing else, which is what makes the assertions below a statement
about what the image can do rather than about what the test runner can do.
Adding ``requirement("cryptography")`` to this target's deps would make it pass
under any ``app_lib``, so do not -- the mutation that proves it works is to
remove that requirement from ``app_lib`` and watch this go red.

The key and token below are a throwaway pair generated for this file. They
are deliberately *not* Cloudflare's: nothing here has to stay in step with the
live key set, and a fixture that did would fail on rotation.
"""

import jwt
import jwt.algorithms
import pytest

# Public half of a throwaway RSA keypair. Building a key object from this is
# the exact operation that fails without cryptography installed.
RSA_JWK = {
    "kid": "vr-test-key",
    "kty": "RSA",
    "alg": "RS256",
    "use": "sig",
    "e": "AQAB",
    "n": (
        "rM5YxvJzSvJyXsyLZs30NyBVWBauz2GZ22TNUOXYkgzZNhBjvpdaZrbkUrI3j9roBAFb"
        "b1-ZLFxnTxcXAe3Q7BV7XSjxon0C_3zUqttWWlidUx7btYHTe-4LnUJXfsDU2BgGhyhu"
        "3Ws9ClawdisPmw4E2nxieMOAVXnk8nw2_sXPcRjVmLj5O0hnZgZSbK8aij9-ujjC_Q3h"
        "xcF962ycVRY2CjxZUSJ3R7Rw8wOPPro63l7GD8EJHlmhoGA-RpY2qy4qempz8buJhEtt"
        "SN_hpxSvRc64sGTwd4C6bH8dremKIrc5reEJQMzJfjDaPN4zMGNn16KhTH4qp6ZjBadp"
        "0Q"
    ),
}

# Signed by the private half. Expired long ago on purpose: expiry is checked
# after the signature, so verifying it with verify_exp off exercises the whole
# RS256 path without the fixture rotting.
TOKEN = (
    "eyJhbGciOiJSUzI1NiIsImtpZCI6InZyLXRlc3Qta2V5IiwidHlwIjoiSldUIn0."
    "eyJzdWIiOiJ1MSIsImF1ZCI6InRlc3QtYXVkIiwiaXNzIjoiaHR0cHM6Ly9leGFtcGxlLmNs"
    "b3VkZmxhcmVhY2Nlc3MuY29tIiwiZW1haWwiOiJzb21lb25lQGV4YW1wbGUuY29tIiwiaWF0"
    "IjoxNzAwMDAwMDAwLCJleHAiOjE3MDAwMDM2MDB9."
    "BRbrCxNazT_ur5Ep3QCmqCCJNpyh8hd8R7rhLnVVUfVH1kxJvZKSV99fmTBTXq-7ga1BVpNy"
    "Jnfb_I-fTXlDjzWFLojcM-RDVH2nokMuXf4f2TIgkns-RdEuL7BW13tLw84Y981EqzCqJia-"
    "psAoy7FDazIP1aBFyE0brMP4OcU_iNAJ0BwUW2RXBvBcRsNmxltd3dEg3yGTK2-dbqYsafNa"
    "QfojGcTNL3T7D1X_LP_b28-YFmeKSahTXPMzB86NsQsNlH7A7vp8ONewQviMEvs79Y-7KnCV"
    "ODlTdfYpiwdwaPqxYhVIZWOW5tnBJ2U1ecSGJEPCsY222UswRDm66g"
)


class TestTheProductionClosureCanVerifyRs256:
    def test_pyjwt_has_its_crypto_backend(self):
        """The cheapest statement of the bug: PyJWT alone is not enough.

        ``jwt.algorithms.has_crypto`` is False when cryptography is absent,
        and every RSA algorithm is then missing from the registry.
        """
        assert jwt.algorithms.has_crypto, (
            "PyJWT has no cryptography backend in this dependency set, so it "
            "cannot verify an RS256 token -- add requirement('cryptography') "
            "to //:app_lib"
        )
        assert "RS256" in jwt.algorithms.get_default_algorithms()

    def test_a_cloudflare_shaped_jwk_builds_a_key(self):
        """What ``_fetch_jwks`` does to every entry Cloudflare publishes."""
        key = jwt.PyJWK.from_dict(RSA_JWK).key
        assert key is not None

    def test_a_token_signed_with_that_key_verifies(self):
        """The whole path, not just key construction.

        ``verify_exp`` is off because the fixture token is static; every other
        check, the signature included, is on.
        """
        claims = jwt.decode(
            TOKEN,
            jwt.PyJWK.from_dict(RSA_JWK).key,
            algorithms=["RS256"],
            audience="test-aud",
            issuer="https://example.cloudflareaccess.com",
            options={"verify_exp": False},
        )
        assert claims["email"] == "someone@example.com"

    def test_a_tampered_token_is_rejected(self):
        """Guards the test above from passing on a no-op verification."""
        head, payload, sig = TOKEN.split(".")
        forged = f"{head}.{payload}.{sig[:-4]}AAAA"
        with pytest.raises(jwt.PyJWTError):
            jwt.decode(
                forged,
                jwt.PyJWK.from_dict(RSA_JWK).key,
                algorithms=["RS256"],
                audience="test-aud",
                issuer="https://example.cloudflareaccess.com",
                options={"verify_exp": False},
            )
