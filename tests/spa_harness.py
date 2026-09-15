"""Shared plumbing for the browser-driven SPA tests.

Kept out of the test module so that the assertions there read as assertions.
"""

import glob
import json
import os
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
    tree = os.environ.get("BAZEL_PLAYWRIGHT_CHROMIUM")
    if not tree:
        return None
    runfiles = os.environ.get("RUNFILES_DIR", "")
    if runfiles and not os.path.isabs(tree):
        tree = os.path.join(runfiles, tree)
    for pattern in ("**/headless_shell", "**/chrome-headless-shell", "**/chrome"):
        for hit in glob.glob(os.path.join(tree, pattern), recursive=True):
            if os.access(hit, os.X_OK) and not os.path.isdir(hit):
                return hit
    raise AssertionError(
        f"BAZEL_PLAYWRIGHT_CHROMIUM is set but no browser binary was found under {tree}")


def index_html_path() -> str:
    runfiles = os.environ.get("RUNFILES_DIR", "")
    if runfiles:
        candidate = os.path.join(runfiles, "_main", "static", "index.html")
        if os.path.exists(candidate):
            return candidate
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(here, "static", "index.html")


class Probe:
    """Reads the fixture server's request log."""

    def __init__(self, base_url: str):
        self.base = base_url

    def log(self) -> list[dict]:
        with urllib.request.urlopen(self.base + "/__probe/log", timeout=10) as r:
            return json.load(r)

    def reset(self) -> None:
        req = urllib.request.Request(self.base + "/__probe/reset", method="POST", data=b"")
        urllib.request.urlopen(req, timeout=10).read()

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


def load_starts(page) -> int:
    return page.evaluate("window.__vrLoads || 0")


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
