"""Browser-driven tests for the signed-in chip.

//:test_initials covers the pure function that turns an address into two
letters. It cannot see whether the chip is ever built, filled or shown, and
those are the parts that would actually be missing from the page. Deleting the
fetch, deleting the markup, or leaving `hidden` set all keep that target green
— so this drives the real page in a real browser and reads the rendered result.

The chip is an indicator over the gate, not the gate. What protects the
deployment is auth.py, exercised in //:test_auth; if any of this is wrong the
worst case is a reviewer who cannot see which account they are signed in as.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fixture_server import serve  # noqa: E402
from spa_harness import find_chromium, index_html_path  # noqa: E402

EMAIL = "andrew.widdowson@apw.photos"

# Marks the moment the page has finished deciding whether to draw the chip.
#
# The signed-in tests need no such thing: they wait for the chip to appear, and
# an appearance is its own event. The signed-out test has the opposite shape --
# it asserts something never happens -- so without a gate it would read the DOM
# before the fetch resolved and pass against a chip that was about to be shown.
# A fixed wait would hide that just as well, which is why this is not one.
#
# It works on microtask ordering rather than on time. This wrapper registers its
# continuation on the fetch promise before the SPA registers its own, so at
# every hop ours is queued first; the extra Promise.resolve() hop then puts the
# flag strictly *after* the SPA's render callback has run. So __vrMe === 'done'
# means the page has already drawn the chip, or already decided not to.
DECISION_MARKER = """
window.__vrMe = 'pending';
(function() {
  var native = window.fetch;
  window.fetch = function(input, init) {
    var p = native.call(this, input, init);
    if (String(input).indexOf('/api/me') !== -1) {
      var settle = function() { window.__vrMe = 'done'; };
      p.then(function(r) { return r.clone().json(); })
       .then(function() { return Promise.resolve(); })
       .then(settle, settle);
    }
    return p;
  };
})();
"""


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        kwargs = {"headless": True}
        chromium = find_chromium()
        if chromium:
            kwargs["executable_path"] = chromium
        b = pw.chromium.launch(**kwargs)
        yield b
        b.close()


def _server(signed_in_email):
    httpd, _ = serve(3, index_html_path(), signed_in_email=signed_in_email)
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


@pytest.fixture
def open_page(browser):
    """Opens the SPA against a fixture that reports a given signed-in state."""
    made = []

    def _open(signed_in_email, path="/owner/repo/pr/1"):
        httpd, base = _server(signed_in_email)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        page.add_init_script(DECISION_MARKER)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base + path, wait_until="load")
        made.append((httpd, ctx, errors))
        return page

    yield _open

    for httpd, ctx, errors in made:
        ctx.close()
        httpd.shutdown()
        assert not errors, f"the page threw: {errors}"


class TestSignedIn:
    def test_the_chip_is_visible_with_the_address(self, open_page):
        page = open_page(EMAIL)
        chip = page.locator("#vr-user")
        chip.wait_for(state="visible", timeout=10_000)
        assert page.locator("#vr-user-email").inner_text() == EMAIL

    def test_the_circle_carries_the_initials(self, open_page):
        page = open_page(EMAIL)
        page.locator("#vr-user").wait_for(state="visible", timeout=10_000)
        assert page.locator("#vr-user-initials").inner_text() == "AW"

    def test_the_chip_is_actually_laid_out(self, open_page):
        """`hidden` is the attribute that matters, and a stylesheet can defeat
        it: any `display` on .vr-user overrides the UA sheet's `hidden` rule,
        which is why .vr-user[hidden] restates `display: none`. A visibility
        check alone would not notice a chip 0px wide, so this reads the box."""
        page = open_page(EMAIL)
        page.locator("#vr-user").wait_for(state="visible", timeout=10_000)
        box = page.locator("#vr-user").bounding_box()
        assert box is not None and box["width"] > 40 and box["height"] > 12, box

    def test_sign_out_points_at_cloudflare_access(self, open_page):
        """The logout path is Cloudflare's, not ours — there is no session of
        our own to end, so a link to anything we serve would look like it
        worked and leave the reviewer signed in."""
        page = open_page(EMAIL)
        page.locator("#vr-user").wait_for(state="visible", timeout=10_000)
        assert page.locator("#vr-user-signout").get_attribute("href") == \
            "/cdn-cgi/access/logout"

    def test_the_chip_survives_an_unparseable_url(self, open_page):
        """The chip is wired ahead of the SPA's URL guard on purpose: a path
        the SPA cannot parse still tells you who you are signed in as."""
        page = open_page(EMAIL, path="/not-a-pr-url/pr/")
        page.locator("#vr-user").wait_for(state="visible", timeout=10_000)
        assert page.locator("#vr-user-email").inner_text() == EMAIL


class TestSignedOut:
    def test_no_chip_when_the_gate_is_off(self, open_page):
        """VR_AUTH_MODE=disabled, i.e. local development. The SPA asks, is told
        nobody is signed in, and draws nothing — rather than an empty chip."""
        page = open_page(None)
        page.wait_for_function("window.__vrMe === 'done'", timeout=10_000)
        assert page.locator("#vr-user").is_hidden()
        assert page.locator("#vr-user-email").inner_text() == ""
