"""Browser-driven tests for the SPA's prefetch behaviour (#21).

Everything in `static/index.html` is an inline script inside an IIFE. Before
this file, the only logic CI could reach was what a test could extract and run
without a DOM: the two pure functions and the tuning that drives them. The
stateful half was mutated green by a reviewer on #18, and the property the
whole feature exists for was asserted by nothing at all.

These tests drive the real page in a real browser against a fake backend.
Three instruments, because no one of them sees everything:

- **The fixture's request log.** A request that reaches the server is one the
  browser could not answer for itself, which is exactly what "renders from
  memory" means. It also counts what a fast scroll left running, and records
  which transfers the browser aborted.
- **A count of load starts inside the page** (`LOAD_COUNTER`). The log is blind
  to a repeated load, because images carry production's `immutable` header and
  the second load is a cache hit. Counting spinner insertions is not.
- **A count of Image constructions** (`IMAGE_COUNTER`), which is what separates
  adopting an in-flight prefetch from restarting it. Both of the above are
  blind to that: the restart is a cache hit, and it is one selectFile call
  either way.

Between them they close the reachability gap that capped #18's structural
checks at twelve. A regex over source can see that `schedulePrefetch()` is
written down; it cannot see whether it runs. `if (false) schedulePrefetch();`
passes there and fails here. Where a test was checked against a named mutant,
that mutant is recorded in its own docstring rather than tallied here; a count
in this docstring would go stale the next time one is added, and did.

Every item on #21's list is covered here but one, the `pendingLoad` clear in
`checkReady`, which is unobservable rather than untested: removing it changes
nothing the page can do, for the reason given beside it in static/index.html.
That was checked rather than reasoned about. A probe in its place that threw if
any image in `pendingLoad` was incomplete, or belonged to another path, stayed
silent across this file and the chip tests three times over, while a control
that threw whenever `pendingLoad` was set there fired 18 times in one run.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fixture_server import BASE_REF, HEAD_REF  # noqa: E402
from spa_harness import (  # noqa: E402
    IMAGE_COUNTER, LOAD_COUNTER, SLOW_IMAGE, active_path,
    constructed_images, issued_requests, load_starts, prefetch_tuning,
    rendered_image_count, repo_root, wait_until,
)

TUNING = prefetch_tuning()

FILE_COUNT = 12
RENAMED_INDEX = 4


def path_of(i: int) -> str:
    return f"shots/file_{i:02d}.png"


# ── the pin the whole harness rests on ──────────────────────────────────────

def test_the_browser_build_matches_the_playwright_pin():
    """`browsers.1.57.0.json` and `playwright==1.57.0` must move together.

    README.md and requirements-test.txt both say so in prose, and prose does
    not fail. Playwright refuses a browser whose revision it does not expect,
    so bumping one without the other turns every test in this file into an
    opaque launch error rather than a sentence naming the mismatch.

    Both halves are checked, and the second is the one that bites. The
    filename carries the version a human reads; `revision` is what actually
    selects the download, since rules_playwright builds the URL as
    `builds/<name>/<revision>/...`. Asserting the filename alone passed
    happily on a manifest whose contents said `1.58.0-MUTANT-LIE`, so the
    exact mistake this exists for — bump playwright, rename the manifest,
    leave the old build behind — survived it. The installed wheel ships its
    own `browsers.json`, which is authoritative and already in the runfiles.
    """
    import glob as _glob
    import importlib.metadata

    import playwright

    manifests = _glob.glob(os.path.join(repo_root(), "browsers.*.json"))
    assert len(manifests) == 1, f"expected exactly one browsers manifest, found {manifests}"
    pinned = os.path.basename(manifests[0]).removeprefix("browsers.").removesuffix(".json")

    installed = importlib.metadata.version("playwright")
    assert pinned == installed, (
        f"the browser manifest is pinned to {pinned} but the playwright package is "
        f"{installed}; update MODULE.bazel and requirements_lock.txt together")

    ours = json.load(open(manifests[0], encoding="utf-8"))["browsers"]
    theirs = json.load(open(os.path.join(
        os.path.dirname(playwright.__file__),
        "driver", "package", "browsers.json"), encoding="utf-8"))["browsers"]
    by_name = {b["name"]: b for b in theirs}

    for browser in ours:
        upstream = by_name.get(browser["name"])
        assert upstream, (
            f"{os.path.basename(manifests[0])} declares {browser['name']}, which "
            f"playwright {installed} does not ship: {sorted(by_name)}")
        for field in ("revision", "browserVersion"):
            assert browser[field] == upstream[field], (
                f"{browser['name']}.{field} is {browser[field]!r} here and "
                f"{upstream[field]!r} in playwright {installed}; re-derive the "
                f"trimmed manifest from the wheel's own browsers.json rather "
                f"than editing it by hand")


# ── fixtures ────────────────────────────────────────────────────────────────

# The shared fixtures (browser, spa_backend, spa_page) are in conftest.py.
# These three adapt them to the shape every test below was written against.

@pytest.fixture
def backend(spa_backend):
    b = spa_backend(file_count=FILE_COUNT, renamed_index=RENAMED_INDEX)
    b.state.image_delay = SLOW_IMAGE
    return b


@pytest.fixture
def base_url(backend):
    return backend.url


@pytest.fixture
def page(spa_page):
    return spa_page(LOAD_COUNTER, IMAGE_COUNTER)


@pytest.fixture
def probe(backend):
    return backend.probe


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
    # rather than in the scroll test below because the scroll cannot see the
    # debounce at all: replacing the timer with a synchronous runPrefetch()
    # leaves that test's peak at 4 transfers racing and its drain unchanged.
    # Note the request total cannot separate a speculation that fired from one
    # that never did: at ahead: 1 the prefetch of file N+1 and the selection of
    # file N+1 ask for the same URL. The total does register the debounce being
    # dropped, by one pair, and the scroll test's docstring says why that pair
    # is not a speculation. (An earlier revision of this comment called that
    # count "the identical 22 requests". It is 20.)
    warm_began = min(r["start"] for r in warmed)
    # Only records that closed *before* the warm started. Taking the max over
    # every record for file 0 couples this to the caching behaviour of the
    # baseline file: a later request for it — which is exactly what happens if
    # the immutable header goes away — makes the gap negative and this
    # assertion fails for a reason that is nothing to do with the debounce.
    landed = [r["end"] for r in probe.images(path_of(0))
              if r["end"] is not None and r["end"] <= warm_began]
    assert landed, "the current pair should have finished before anything is warmed"

    gap = warm_began - max(landed)
    # Read from PREFETCH_TUNING rather than restated: a retune to 240ms is the
    # author's to make and must move this expectation, not fail it. The 20%
    # slack is for the timer firing late, never early.
    #
    # Scaling to the knob does mean `delayMs: 0` makes this assertion vacuous.
    # That floor is held elsewhere and deliberately: //:test_prefetch_policy
    # asserts `delayMs > 0` ("prefetching must be debounced"), so between them
    # one test says a debounce must exist and this one says the configured
    # value is honoured. Checked — `delayMs: 0` is red over there.
    expected = TUNING["delayMs"] / 1000
    assert gap >= expected * 0.8, \
        f"the warm began {gap:.3f}s after the current pair landed; the debounce " \
        f"is meant to hold it for {expected:.2f}s"

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

    above, below = f"file_{start - 1:02d}", f"file_{start + 1:02d}"

    def issued(name: str) -> list[int]:
        return [i for i, url in enumerate(issued_requests(page)) if name in url]

    # >=, not ==: a third request for either file would make an == wait spin to
    # its timeout and report as "never warmed", which is the opposite of what
    # happened.
    wait_until(lambda: len(issued(above)) >= 2, "both sides of the file above to be warmed")
    wait_until(lambda: len(issued(below)) >= 2, "both sides of the file below to be warmed")

    # Next before previous, so a reader going forwards never waits on a warm
    # they did not ask for. Read off the renderer's issue order, not the
    # fixture's arrival times: the fixture stamps `start` inside a handler
    # thread, so four near-simultaneous requests are ordered by the OS
    # scheduler and a sub-millisecond margin means nothing. One run had the
    # two 325 microseconds apart, the wrong way round.
    assert min(issued(below)) < min(issued(above)), \
        "the next file should be requested before the previous one"


def test_a_fast_scroll_does_not_pile_up_transfers(page, probe, base_url):
    """A quick scroll leaves the landed file loading and little else.

    Before #18 every file passed through kept two transfers racing the one the
    user stopped on. What clears them here is the abort: each selection cancels
    the last one's transfers. Not the debounce — replacing the timer with a
    synchronous runPrefetch() leaves the peak at 4 racing and the drain
    unchanged, so nothing below moves.

    It does move the request count, by exactly one pair: 20 issued rather than
    22, in 5 runs of each. Not a prefetch landing mid-scroll — the pair that
    goes missing is file 1's, warmed before the scroll starts, because open_pr
    returns when the first pair renders and the debounce holds that warm past
    the probe.reset() below. Measured per path, not reasoned about; an earlier
    revision of this docstring claimed the count was identical, which it is
    not. The bound below therefore has real slack, so that a warm landing
    anywhere in the scroll cannot fail it.
    """
    open_pr(page, base_url)
    probe.reset()

    # Sampled during the scroll, not after it, and for two reasons. It is the
    # positive control on the instrument: an upper bound of "<= 2 racing" is
    # satisfied by any reader that under-reports, `return []` included, so
    # without this the test's own measuring device is unpinned. And a mid-scroll
    # sample is the only place the races actually exist.
    high_water = 0
    for _ in range(FILE_COUNT - 1):
        page.keyboard.press("j")
        page.wait_for_timeout(75)
        high_water = max(high_water, len(probe.in_flight()))

    assert active_path(page) == path_of(FILE_COUNT - 1)
    # 2, not 1, so that the floor meets the ceiling. The bound asserted below is
    # "<= 2 racing", and a reader capped at 1 cannot fail it — which is what an
    # earlier `>= 1` here left open. A pair is two images and the fixture's delay
    # is a deterministic sleep, so two in flight at a mid-scroll sample is
    # guaranteed by construction; measured 4 in 15 runs of 16 and 3 in the other.
    # A reader capped at exactly 2 still passes both, and no control of this
    # shape can exclude one: the floor cannot go above the ceiling it defends.
    # That residue is irreducible here rather than an oversight.
    assert high_water >= 2, \
        f"the in-flight reader peaked at {high_water} during a scroll of " \
        f"{FILE_COUNT - 1} files, so it cannot tell a drained stack from a " \
        "racing one and the bound below asserts nothing"

    # Bounded re-sample rather than one reading. An aborted transfer stays open
    # in the log until the fixture's next write into that socket raises, which
    # is up to a slice-time later — so a single sample taken the instant the
    # scroll stops counts the previous file's pair as still racing when the
    # browser has already cancelled it. What is being asserted is that they
    # drain, not that the server has already noticed.
    wait_until(lambda: len(probe.in_flight()) <= 2,
               "the racing transfers to drain to the landed pair",
               timeout=SLOW_IMAGE * 4)

    # The other half of the bound: the scroll really did issue the traffic it
    # was supposed to, so "<= 2 left" is not the emptiness of a scroll that
    # never happened. Deliberately far below the 22 this measures, because that
    # is the whole of what this half is for. At 22 the bound was an equality
    # wearing a >=, with zero slack in the one direction a landed warm, a faster
    # machine or a smaller SLOW_IMAGE all push: dropping the debounce takes it
    # to 20 and made this assertion, in a test whose docstring says it cannot
    # see the debounce, the strongest debounce detector in the file.
    issued = probe.images()
    assert len(issued) >= FILE_COUNT, \
        f"a scroll through {FILE_COUNT - 1} files should have issued at least " \
        f"{FILE_COUNT} requests, saw {len(issued)}"

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

# Adoption — a prefetch the user catches up with being reused rather than
# restarted — is covered at the bottom of this file, and it took a third
# instrument to do it. Neither of the two above can see it: the request log
# cannot, because a restarted load is a cache hit that never reaches the
# server, and the load counter cannot, because there is one selectFile call
# either way. An earlier attempt with PerformanceResourceTiming read the same
# under both, and why was never established. IMAGE_COUNTER counts Image
# constructions instead, which is the thing the adoption path actually
# changes: 2 when it works, 4 when it does not.


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

    wait_until(lambda: len(probe.images(f"{RENAMED_INDEX:02d}.png")) == 2,
               "both sides of the renamed file to be warmed")
    by_ref = {r["ref"]: r["path"] for r in probe.images(f"{RENAMED_INDEX:02d}.png")}
    assert by_ref.get(BASE_REF) == f"shots/old_name_{RENAMED_INDEX:02d}.png", \
        f"the base side should be warmed at the previous path, got {by_ref}"
    assert by_ref.get(HEAD_REF) == path_of(RENAMED_INDEX), \
        f"the head side should be warmed at the current path, got {by_ref}"


def test_an_in_flight_prefetch_is_adopted_rather_than_restarted(page, probe, base_url):
    """Arriving on a file mid-warm reuses that transfer instead of starting a second.

    The last item of #21's list, and the one that needed a third instrument.
    Neither of the other two can see it. The request log cannot, because the
    images carry production's `immutable` header, so a restarted load is a
    cache hit that never reaches the server. The load counter cannot either:
    it counts selectFile calls, and there is exactly one here whichever way
    the page behaves. What separates them is how many Image objects the page
    builds, which IMAGE_COUNTER counts by wrapping the constructor before any
    page script runs.

    Waiting for the warm to have *started* is load-bearing rather than
    tidiness: press j before it does and the page builds a fresh pair for the
    honest reason, two either way, so the Image count below cannot tell an
    adoption path that works from one that does nothing. The wait is what
    makes the block reachable. Delete it and the racing assertion catches the
    vacuous configuration and says so — checked both with `usableCached`
    returning null and on an unmutated page, RED on `saw []` either way.
    Mutants: `usableCached -> return null` and the `exceptPath` guard deleted
    from `cancelPrefetches`, both RED at 4 objects against the 2 asserted.
    """
    open_pr(page, base_url)
    wait_until(lambda: len(probe.images(path_of(1))) == 2, "the warm to be in flight")

    # Logged is not the same as open, and "mid-warm" is this test's whole
    # identity: if the warm ever finished before the keypress, the test would
    # quietly become a second copy of the already-complete path with a name
    # and a docstring that still said otherwise. It asks nothing new of the
    # page — both sides measured open on every run — but it does tighten the
    # test: without it, removing the wait above leaves this passing on two
    # honestly-built images, which is the vacuous run check 1 asks about.
    racing = [r["path"] for r in probe.in_flight()]
    assert racing.count(path_of(1)) == 2, \
        f"both sides of file 1 should still be in flight at the keypress, saw {racing}"

    page.keyboard.press("j")
    wait_until(lambda: active_path(page) == path_of(1), "file 1 to be selected")
    wait_until(lambda: rendered_image_count(page) == 2, "file 1 to render")

    built = constructed_images(page, "file_01.png")
    assert len(built) == 2, (
        f"the page should have built one pair of Image objects for file 1, the "
        f"pair the prefetch started, but it built {len(built)}: "
        f"{[[s.split('?', 1)[-1] for s in srcs] for srcs in built]}")


def test_moving_away_mid_warm_keeps_the_half_that_arrived(page, backend):
    """An abandoned warm aborts the side still in flight and keeps the other.

    `cancelPrefetches` runs on every selection. For a warm that had half
    landed, it drops only the side that was still loading, so coming back to
    that file reuses the decoded half and fetches the other. Built objects are
    the instrument, as for adoption above: 3 for file 1 when the arrived half
    is kept (the warm's pair, then one fresh image for the side that was
    aborted), 4 when the whole entry is thrown away.

    The interleaving is driven by holds rather than timing. File 1's head is
    held so that its base can land while the head is provably still open, and
    file 3 is held whole so that it never renders: a render would run the
    prefetch plan, whose eviction could drop file 1 for a reason that has
    nothing to do with `cancelPrefetches`. File 3 rather than file 2 because
    file 2 is next to file 1 and would be warmed on arrival back there.

    Mutant: `if (entry) delete state.imageCache[path]` in place of the
    both-sides-gone check, which is the "drop the whole pair" shape — RED at 4.
    """
    state = backend.state
    held_head = state.hold("image", path_of(1), ref=HEAD_REF)
    state.hold("image", path_of(3))

    open_pr(page, backend.url)
    assert active_path(page) == path_of(0)

    def base_landed():
        return [r for r in backend.probe.images(path_of(1))
                if r["ref"] == BASE_REF and r["end"] is not None and not r["aborted"]]

    wait_until(base_landed, "file 1's base to be warmed and delivered")
    wait_until(held_head.reached.is_set, "file 1's head to be requested and held")

    page.click(f'.file-item[data-path="{path_of(3)}"]')
    wait_until(lambda: active_path(page) == path_of(3), "file 3 to be selected")

    # The abort is the half being let go of, and seeing it is what makes this
    # a test of the in-flight branch: if the head had somehow landed first,
    # both halves would be kept for the honest reason and 3 would read as 2.
    wait_until(lambda: any(r["ref"] == HEAD_REF and r["aborted"]
                           for r in backend.probe.images(path_of(1))),
               "the move away to abort file 1's held head")

    held_head.release()
    page.click(f'.file-item[data-path="{path_of(1)}"]')
    wait_until(lambda: active_path(page) == path_of(1), "file 1 to be selected")
    wait_until(lambda: rendered_image_count(page) == 2, "file 1 to render")

    built = constructed_images(page, "file_01.png")
    assert len(built) == 3, (
        f"file 1 should have cost three Image objects -- the warm's pair, then "
        f"one for the head it lost -- but it cost {len(built)}: "
        f"{[[s.split('?', 1)[-1] for s in srcs] for srcs in built]}")
    # Not also checked through the request log: under the `immutable` header a
    # rebuilt base is a cache hit that never reaches the fixture, so a count of
    # base requests reads 1 whether or not the page kept the decoded image.
