"""Browser-driven tests for the SPA's prefetch behaviour (#21).

Everything in `static/index.html` is an inline script inside an IIFE. Before
this file, the only logic CI could reach was what a test could extract and run
without a DOM: the two pure functions and the tuning that drives them. The
stateful half was mutated green by a reviewer on #18, and the property the
whole feature exists for was asserted by nothing at all.

These tests drive the real page in a real browser against a fake backend. Two
instruments, because one is not enough:

- **The fixture's request log.** A request that reaches the server is one the
  browser could not answer for itself, which is exactly what "renders from
  memory" means. It also counts what a fast scroll left running, and records
  which transfers the browser aborted.
- **A count of load starts inside the page** (`LOAD_COUNTER`). The log is blind
  to a repeated load, because images carry production's `immutable` header and
  the second load is a cache hit. Counting spinner insertions is not.

Between them they close the reachability gap that capped #18's structural
checks at twelve. A regex over source can see that `schedulePrefetch()` is
written down; it cannot see whether it runs. `if (false) schedulePrefetch();`
passes there and fails here — checked, along with eight other mutants.

One thing on #21's list is still not covered, and the comment above
`test_each_selection_costs_exactly_one_load` says why: whether an in-flight
prefetch is *adopted* rather than restarted is not observable from outside the
page under either instrument.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fixture_server import BASE_REF, HEAD_REF, serve  # noqa: E402
from spa_harness import (  # noqa: E402
    LOAD_COUNTER, SLOW_IMAGE, Probe, active_path, find_chromium,
    index_html_path, load_starts, rendered_image_count, wait_until,
)

FILE_COUNT = 12
RENAMED_INDEX = 4


def path_of(i: int) -> str:
    return f"shots/file_{i:02d}.png"


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def base_url():
    httpd, state = serve(FILE_COUNT, index_html_path(), renamed_index=RENAMED_INDEX)
    state.image_delay = SLOW_IMAGE
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


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


@pytest.fixture
def page(browser):
    # A fresh context per test: the point of several of these is what the page
    # kept in memory, and a shared context would carry it between them.
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    p = ctx.new_page()
    p.add_init_script(LOAD_COUNTER)
    errors = []
    p.on("pageerror", lambda e: errors.append(str(e)))
    yield p
    ctx.close()
    assert not errors, f"the page threw: {errors}"


@pytest.fixture
def probe(base_url):
    pr = Probe(base_url)
    pr.reset()
    return pr


def open_pr(page, base_url: str, hash_suffix: str = "") -> None:
    page.goto(f"{base_url}/owner/repo/pr/1{hash_suffix}", wait_until="load")
    wait_until(lambda: active_path(page) is not None, "a file to be selected")
    wait_until(lambda: rendered_image_count(page) > 0, "the first pair to render")


# ── the property the feature exists for ─────────────────────────────────────

def test_the_next_file_is_warmed_while_you_read_this_one(page, probe, base_url):
    """Sitting on a file fetches the next one, unprompted and invisibly."""
    open_pr(page, base_url)
    assert active_path(page) == path_of(0)

    wait_until(lambda: len(probe.images(path_of(1))) == 2, "file 1 to be prefetched")
    warmed = probe.images(path_of(1))
    assert len({r["ref"] for r in warmed}) == 2, \
        f"both sides of file 1 should be warmed, got {[r['ref'] for r in warmed]}"

    # And warmed *after a pause*, not the instant the current pair lands. This
    # is the whole of the debounce's observable contract: a fast scroll must
    # not speculate on a file it only passes through. It is asserted here
    # rather than in the scroll test below because during a fast scroll the
    # abort already prevents the speculation — measured, with the debounce
    # removed, as the identical 22 requests — so the scroll cannot see it.
    landed = [r["end"] for r in probe.images(path_of(0)) if r["end"]]
    assert landed, "the current pair should have finished before anything is warmed"
    current_done = max(landed)
    warm_began = min(r["start"] for r in warmed)
    gap = warm_began - current_done
    assert gap >= 0.20, \
        f"the warm began {gap:.3f}s after the current pair landed; the debounce " \
        f"is meant to hold it for {0.25:.2f}s"

    # Invisibly: the selection has not moved and nothing on screen shows the
    # warmed file. A prefetch the reviewer can see happening is a bug.
    assert active_path(page) == path_of(0)
    on_screen = page.eval_on_selector_all(
        "#viewport img", "els => els.map(e => e.getAttribute('src') || '')")
    assert not any("file_01" in src for src in on_screen), \
        f"the warmed file should not be displayed, but the viewport shows {on_screen}"


def test_the_next_file_renders_without_reaching_the_server(page, probe, base_url):
    """The headline. `j` onto a warmed file issues no request at all.

    This is the assertion #18 shipped without, and the one a regression in the
    prefetch would fail while every other test stayed green.
    """
    open_pr(page, base_url)
    wait_until(lambda: len(probe.images(path_of(1))) == 2, "file 1 to be fully warmed")
    wait_until(lambda: not probe.in_flight(), "the warm to finish")

    probe.reset()
    page.keyboard.press("j")

    wait_until(lambda: active_path(page) == path_of(1), "file 1 to become current")
    wait_until(lambda: rendered_image_count(page) == 2, "file 1 to render")

    fetched = [r for r in probe.images() if r["path"] == path_of(1)]
    assert fetched == [], \
        f"file 1 was already in memory but the page re-fetched it: {fetched}"


def test_the_previous_file_is_warmed_too(page, probe, base_url):
    """j and k both go somewhere, so the file above is warmed as well.

    Second, not first — the plan orders next before previous — but warmed.
    """
    # Away from RENAMED_INDEX in both directions: the renamed file's base side
    # is warmed at its previous path, which this test's path filter would miss.
    start = 8
    open_pr(page, base_url, hash_suffix=f"#file_{start:02d}.png")
    wait_until(lambda: active_path(page) == path_of(start), "the deep link to select its file")

    wait_until(lambda: len(probe.images(path_of(start - 1))) == 2,
               "the file above to be warmed")
    wait_until(lambda: len(probe.images(path_of(start + 1))) == 2,
               "the file below to be warmed")

    # Next before previous, so a reader going forwards never waits on a warm
    # they did not ask for.
    assert (min(r["start"] for r in probe.images(path_of(start + 1)))
            < min(r["start"] for r in probe.images(path_of(start - 1)))), \
        "the next file should be requested before the previous one"


def test_a_fast_scroll_does_not_pile_up_transfers(page, probe, base_url):
    """A quick scroll leaves the landed file loading and little else.

    Before #18 every file passed through kept two transfers racing the one the
    user stopped on. What clears them here is the abort: each selection cancels
    the last one's transfers. The debounce is not what this measures — with it
    removed the scroll issued the identical 22 requests, because a file passed
    through never finishes rendering and so never reaches the code that arms a
    prefetch at all. The debounce is pinned by the timing assertion in the first
    test instead.
    """
    open_pr(page, base_url)
    probe.reset()

    for _ in range(FILE_COUNT - 1):
        page.keyboard.press("j")
        page.wait_for_timeout(75)

    assert active_path(page) == path_of(FILE_COUNT - 1)
    in_flight = probe.in_flight()
    assert len(in_flight) <= 2, \
        f"{len(in_flight)} transfers still racing after the scroll: {in_flight}"

    wait_until(lambda: rendered_image_count(page) == 2, "the landed file to render")


def test_abandoned_transfers_are_cancelled(page, probe, base_url):
    """Moving on mid-load closes the connection rather than letting it finish.

    The fixture records an abort when a write into a half-delivered response
    raises, so this is the browser's own behaviour and not an inference.
    """
    open_pr(page, base_url)
    probe.reset()

    for _ in range(6):
        page.keyboard.press("j")
        page.wait_for_timeout(60)

    wait_until(probe.aborted, "at least one transfer to be aborted")


# ── the stateful paths a source-text check cannot see ───────────────────────

# Not tested here: that a prefetch the user catches up with is *adopted* rather
# than restarted. With `usableCached` stubbed to return null — adoption off —
# this whole file stays green, and a probe counting PerformanceResourceTiming
# entries for the URL read the same either way. So neither instrument sees it.
# The request log certainly cannot: a restarted load is a cache hit and never
# reaches the server. Why resource timing did not separate them was not
# established, and the probe's timing may simply have missed the in-flight
# window; it is recorded as unverified rather than explained away.
#
# A test that passes whatever the code does is worse than an acknowledged gap,
# so this one is left to #21 instead of written.


def test_each_selection_costs_exactly_one_load(page, probe, base_url):
    """selectFile writes the hash; the resulting hashchange must not re-select.

    Without the re-entry guard every selection cost two full loads, the first
    one on page load included. Measured by counting load starts in the page —
    see LOAD_COUNTER for why neither the request log nor resource timing can
    see this.
    """
    open_pr(page, base_url)
    assert load_starts(page) == 1, \
        f"opening the PR should start one load, not {load_starts(page)}"

    page.keyboard.press("j")
    wait_until(lambda: active_path(page) == path_of(1), "file 1 to become current")
    wait_until(lambda: rendered_image_count(page) == 2, "file 1 to render")
    assert load_starts(page) == 2, \
        f"one keypress should add one load, but the total is {load_starts(page)}"


def test_a_renamed_file_is_warmed_at_its_previous_path(page, probe, base_url):
    """The base side of a renamed file lives at previous_filename.

    The prefetch used to build both sides from the current path, so every
    renamed file warmed a 404 and then had to be fetched properly on arrival.
    """
    open_pr(page, base_url, hash_suffix=f"#file_{RENAMED_INDEX - 1:02d}.png")
    wait_until(lambda: active_path(page) == path_of(RENAMED_INDEX - 1), "the file above the rename")

    warmed = wait_until(lambda: probe.images(f"{RENAMED_INDEX:02d}.png"),
                        "the renamed file to be warmed")
    by_ref = {r["ref"]: r["path"] for r in warmed}
    assert by_ref.get(BASE_REF) == f"shots/old_name_{RENAMED_INDEX:02d}.png", \
        f"the base side should be warmed at the previous path, got {by_ref}"
    assert by_ref.get(HEAD_REF) == path_of(RENAMED_INDEX), \
        f"the head side should be warmed at the current path, got {by_ref}"
