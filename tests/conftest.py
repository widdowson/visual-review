"""Shared fixtures for the browser-driven SPA tests.

Three of them, and a new browser test should need nothing else:

- `browser` — one hermetic Chromium for the whole session.
- `spa_backend` — a factory that starts `tests/fixture_server.py` with
  whatever payload the test describes, and shuts it down afterwards.
- `spa_page` — a factory for a fresh page in a fresh context, which fails the
  test if the page threw.

A new target picks these up by going through `spa_browser_test` in
`//:browser_test.bzl`, which puts this file in its srcs. Playwright and the
fixture server are imported inside the fixtures rather than at the top: the
server imports app.py, whose Access config is read at import, and neither
should be a precondition of `pytest tests/` collecting the tests that have no
browser.
"""

import os
import sys
from dataclasses import dataclass

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from spa_harness import Probe, find_chromium, index_html_path  # noqa: E402


@dataclass
class Backend:
    """A running fixture: where it is, what it serves, and what it saw.

    `state` is the server's FixtureState, shared with its handler threads, so
    a test can hold a response or read what was served without going through
    HTTP. `probe` reads the same log over HTTP, which is how the tests written
    before this existed read it.
    """
    url: str
    state: object
    probe: Probe

    def pr_url(self, suffix: str = "") -> str:
        return f"{self.url}/owner/repo/pr/1{suffix}"


@pytest.fixture(scope="session")
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


@pytest.fixture
def spa_backend():
    """Start a fixture backend: `spa_backend(files=[...], comments={...})`.

    Keyword arguments are FixtureState's. Every hold is released at teardown
    before the server stops, so a test that fails mid-hold does not leave a
    handler thread parked on it for the rest of the session.
    """
    started = []

    from fixture_server import serve

    def _start(**state_kwargs) -> Backend:
        httpd, state = serve(index_html_path(), **state_kwargs)
        url = f"http://127.0.0.1:{httpd.server_address[1]}"
        started.append((httpd, state))
        return Backend(url, state, Probe(url))

    yield _start

    for httpd, state in started:
        state.release_all()
        httpd.shutdown()
        httpd.server_close()


# spa_backend is requested only for its teardown order: pytest tears fixtures
# down in reverse, so depending on it closes every page before any server stops,
# and a page never sees its backend vanish under it.
@pytest.fixture
def spa_page(browser, spa_backend):
    """A page in a fresh context: `spa_page(INIT_SCRIPT, ...)`.

    A fresh context per page, because several tests are about what the page
    kept in memory and a shared context would carry it between them. Init
    scripts run before any page script, in the order given.

    `page.vr_requests` is every URL the renderer asked for, in issue order —
    read it through `spa_harness.issued_requests`, which says why the
    round-trip matters. An uncaught exception in the page fails the test at
    teardown, whatever the test itself asserted.
    """
    made = []

    def _open(*init_scripts: str, viewport: dict | None = None):
        ctx = browser.new_context(viewport=viewport or {"width": 1280, "height": 900})
        page = ctx.new_page()
        for script in init_scripts:
            page.add_init_script(script)
        page.vr_requests = []
        page.on("request", lambda r: page.vr_requests.append(r.url))
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        made.append((ctx, errors))
        return page

    yield _open

    thrown = []
    for ctx, errors in made:
        ctx.close()
        thrown.extend(errors)
    assert not thrown, f"the page threw: {thrown}"
