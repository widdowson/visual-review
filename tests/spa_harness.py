"""Shared plumbing for the browser-driven SPA tests.

Kept out of the test module so that the assertions there read as assertions.
"""

import glob
import json
import os
import re
import time
import urllib.request

# Long enough that a transfer is observably in flight for the whole of a
# keypress, short enough that a dozen of them do not dominate the suite.
SLOW_IMAGE = 0.30


def find_chromium() -> str | None:
    """The hermetic Chromium from @playwright//:chromium-headless-shell.

    Bazel passes its rlocationpath; resolve that against RUNFILES_DIR. Returns
    None outside Bazel so the file can also be run against a locally installed
    browser while developing.
    """
    rlocation = os.environ.get("BAZEL_PLAYWRIGHT_CHROMIUM")
    if not rlocation:
        return None

    # RUNFILES_DIR is absent under a manifest-only runfiles tree (which is what
    # --nobuild_runfile_links gives you). Falling back to TEST_SRCDIR keeps the
    # failure about the browser rather than about the variable.
    roots = [r for r in (os.environ.get("RUNFILES_DIR"),
                         os.environ.get("TEST_SRCDIR")) if r]
    trees = [rlocation] if os.path.isabs(rlocation) else [
        os.path.join(root, rlocation) for root in roots] or [rlocation]

    for tree in trees:
        for pattern in ("**/headless_shell", "**/chrome-headless-shell", "**/chrome"):
            for hit in glob.glob(os.path.join(tree, pattern), recursive=True):
                if os.access(hit, os.X_OK) and not os.path.isdir(hit):
                    return hit
    raise AssertionError(
        "BAZEL_PLAYWRIGHT_CHROMIUM is set but no browser binary was found under "
        + " or ".join(trees))


def repo_root() -> str:
    """The repo root, under Bazel runfiles or in a plain checkout.

    Probed with a file every caller has in its runfiles rather than with the
    caller's own argument. repo_file() used to do the latter, so a caller
    passing a glob could never satisfy os.path.exists and fell through to the
    checkout branch — which lands inside the runfiles tree anyway, so it
    returned the right answer through the branch that means "not under Bazel".
    """
    for root in (os.environ.get("RUNFILES_DIR"), os.environ.get("TEST_SRCDIR")):
        if root and os.path.exists(os.path.join(root, "_main", "static", "index.html")):
            return os.path.join(root, "_main")
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def repo_file(*parts: str) -> str:
    """A path to a file at the repo root. Glob against repo_root() instead."""
    return os.path.join(repo_root(), *parts)


def index_html_path() -> str:
    return repo_file("static", "index.html")


def prefetch_tuning() -> dict[str, int]:
    """PREFETCH_TUNING, read out of the marked region of static/index.html.

    So a test can state the debounce's contract without restating its value.
    A retune to 240ms is a judgement call the author is allowed to make, and it
    must move this test's expectation with it rather than failing it.

    Each key must appear exactly once, because a commented-out previous value
    sitting above a live one would otherwise read as the live one. The line
    comments stripped to get there are a narrower rule than the one
    tests/spa_source.js applies -- that one also keeps a `://` out of it and
    handles `/* */`, and this does neither. Enough for the region it reads,
    but not the same rule, so it does not claim to be.

    `(\\d+)` takes the leading digits and ignores the rest, so a non-integer
    would be truncated in silence: `delayMs: 0.5` reads as 0, which makes the
    scaled assertion in the first test vacuous with nothing saying so. What
    stops that is in another target -- //:test_prefetch_policy asserts
    Number.isInteger over every key and fires first -- and it is named here
    for the same reason the `delayMs > 0` floor is named at its use.
    """
    src = open(index_html_path(), encoding="utf-8").read()
    begin = src.index("prefetch-policy:begin")
    end = src.index("prefetch-policy:end")
    region = re.sub(r"//.*$", "", src[begin:end], flags=re.M)

    body = re.search(r"PREFETCH_TUNING\s*=\s*\{(.*?)\}", region, re.S)
    assert body, "static/index.html must define a PREFETCH_TUNING object literal"

    tuning = {}
    for key, value in re.findall(r"(\w+)\s*:\s*(\d+)", body.group(1)):
        assert key not in tuning, f"PREFETCH_TUNING.{key} is declared twice"
        tuning[key] = int(value)
    assert set(tuning) == {"ahead", "behind", "delayMs", "cacheRadius"}, \
        f"PREFETCH_TUNING changed shape: {sorted(tuning)}"
    return tuning


class Probe:
    """Reads the fixture server's request log."""

    def __init__(self, base_url: str):
        self.base = base_url
        # An explicitly empty ProxyHandler, because the module-level urlopen
        # honours http_proxy from the environment. Bazel scrubs those today, so
        # this is latent rather than broken — but a --test_env or an
        # --action_env on some future runner would send a loopback probe to a
        # proxy, and the failure would look like the fixture being broken.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def log(self) -> list[dict]:
        with self.opener.open(self.base + "/__probe/log", timeout=10) as r:
            return json.load(r)

    def reset(self) -> None:
        req = urllib.request.Request(self.base + "/__probe/reset", method="POST", data=b"")
        self.opener.open(req, timeout=10).read()

    def images(self, path_fragment: str = "") -> list[dict]:
        return [r for r in self.log()
                if r["kind"] == "image" and path_fragment in r["path"]]

    def in_flight(self) -> list[dict]:
        return [r for r in self.log() if r["kind"] == "image" and r["end"] is None]

    def aborted(self) -> list[dict]:
        return [r for r in self.log() if r.get("aborted")]


def wait_until(predicate, message: str, timeout: float = 15.0, interval: float = 0.05):
    """Poll `predicate` until it returns something truthy, or fail saying why.

    Every gate in these tests is a real condition rather than a sleep. The one
    exception is the debounce itself, which is a deliberate delay in the code
    under test — waiting for its *effect* (a request appearing in the log) is
    what this is for.
    """
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"timed out after {timeout}s waiting for: {message}")


# Counts how many times the SPA starts loading a pair. `selectFile` drops a
# spinner into the viewport before calling `loadImagePair`, and that insertion
# is the one discrete, observable event per load — one per selection, and two
# when a selection re-enters itself.
#
# It exists because the obvious instrument does not work. Counting requests at
# the fixture cannot see a repeated load at all: images are served with
# production's `immutable` header, so the second load is a cache hit and
# nothing reaches the server. Counting PerformanceResourceTiming entries fails
# for a subtler reason — every comparison mode builds fresh <img> elements from
# `state.baseImg.src`, so each render adds entries of its own and a repeat is
# not distinguishable from a re-render. Both were tried against a tree with the
# re-entry guard deleted, and both stayed green.
LOAD_COUNTER = """
window.__vrLoads = 0;
document.addEventListener('DOMContentLoaded', () => {
  const vp = document.getElementById('viewport');
  if (!vp) return;
  new MutationObserver(ms => ms.forEach(m => {
    m.addedNodes.forEach(n => {
      if (n.nodeType === 1 && n.classList && n.classList.contains('spinner')) {
        window.__vrLoads++;
      }
    });
  })).observe(vp, {childList: true});
});
"""


IMAGE_COUNTER = """
window.__vrImgs = [];
(function () {
  var Real = window.Image;
  var desc = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, 'src');
  function Counted(width, height) {
    var img = new Real(width, height);
    // Strings only. The element itself is not retained: nothing reads it, and
    // holding one would pin every Image the page builds alive for the life of
    // the page, evicted ones included, in a file that also tests cacheRadius.
    var rec = {srcs: []};
    window.__vrImgs.push(rec);
    // Record each assignment rather than reading the attribute back later.
    // abortImage() does `img.src = ''`, and the IDL getter then resolves the
    // empty string against the document, so an aborted image reads back as the
    // page URL with every trace of what it was fetching gone.
    Object.defineProperty(img, 'src', {
      configurable: true,
      enumerable: true,
      get: function () { return desc.get.call(img); },
      set: function (value) { rec.srcs.push(String(value)); desc.set.call(img, value); },
    });
    return img;
  }
  Counted.prototype = Real.prototype;
  window.Image = Counted;
  // Three divergences left standing, none reachable from static/index.html:
  // calling Image() without new returns instead of throwing;
  // String(window.Image) is not [native code]; and the defineProperty above
  // gives the image an *own* enumerable `src` where a real one inherits it,
  // so Object.keys(img), JSON.stringify(img) and {...img} all show it. The
  // page never enumerates an Image — its Object.keys/for-in/JSON.stringify
  // sites are over state.prefetching, state.imageCache, the comment counts
  // and a POST body. The divergence that would have bitten — new Image(w, h)
  // losing its dimensions — is forwarded above, measured [37, 41] through the
  // wrapper against [0, 0] for the no-arg form.
})();
"""


def constructed_images(page, fragment: str) -> list[list[str]]:
    """Every Image the page built for `fragment`, as the srcs assigned to each.

    The third instrument, and the only one that can separate adopting an
    in-flight prefetch from restarting it. The request log cannot: the images
    carry production's `immutable` header, so a restart is a cache hit that
    never reaches the server. LOAD_COUNTER cannot either: it counts selectFile
    calls, and there is one of those whichever way the page behaves. What does
    change is how many Image objects get built, and that is what this counts.

    Three things it deliberately does not see. It wraps `window.Image`, so the
    img elements renderComparison builds with createElement are not counted —
    the decode path alone is the question here. It records srcs **as they are
    assigned**, never by reading `img.src` back: `abortImage` clears the
    attribute, so a read-back count silently drops every image the page
    aborted, in the direction that passes. An earlier revision did read it
    back, and a mutant that aborted the adoption target and restarted it went
    green. And because it records assignments to the *property*, a src set
    with `setAttribute` is invisible to it — an image fetched only that way
    would drop out of the filter, the same false-pass direction. Unreachable
    today (`setAttribute` appears 11 times in static/index.html, never for
    src; every src is a property assignment) and the one thing the read-back
    version did cover, so it is stated rather than guarded against.

    A record matches `fragment` if any src ever assigned to it did, so an
    aborted-then-cleared image still counts. `fragment` is required because
    `'' in s` is true of every string: an empty one would match every image
    built, including one whose only recorded assignment is abortImage's clear.
    """
    records = page.evaluate("window.__vrImgs.map(function (r) { return r.srcs; })")
    return [srcs for srcs in records if any(fragment in src for src in srcs)]


def load_starts(page) -> int:
    return page.evaluate("window.__vrLoads || 0")


def issued_requests(page) -> list[str]:
    """Every URL the renderer has asked for, in the order it asked.

    The round-trip is not decoration. Playwright's sync API dispatches queued
    CDP events only when the main thread calls into it, and the waits in these
    tests poll the fixture over HTTP rather than the page — so without a call
    into Playwright the listener's list stays empty however long you wait, and
    an ordering assertion reads it as "the page never requested that".
    """
    page.evaluate("0")
    return list(page.vr_requests)


def active_path(page) -> str | None:
    el = page.query_selector(".file-item.active")
    return el.get_attribute("data-path") if el else None


def rendered_image_count(page) -> int:
    """Images in the viewport that have actually decoded."""
    return page.evaluate("""() => {
        const imgs = document.querySelectorAll('#viewport img');
        let n = 0;
        imgs.forEach(i => { if (i.complete && i.naturalWidth > 0) n++; });
        return n;
    }""")
