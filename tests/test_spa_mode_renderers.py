"""Browser-driven tests for the four comparison-mode renderers (#40).

//:test_rename_display runs `renderComparison` and the rename decision against
a stub DOM, and stubs the four mode renderers out: standing them up there
means stubbing the loupes, the diff gutter and requestAnimationFrame. So two
things #28 depends on rested on source-text checks at the end of that file.
Both are asserted here on what the real page renders, and those checks are
gone.

1. **What an empty base panel says.** Only an added file legitimately has no
   base; a rename whose base failed to load keeps the modes and must not be
   called a new file.
2. **The diff gutter does not compare alpha.** `computeRowDiffMap` takes
   `includeAlpha` so the rename decision can ask for it without changing what
   the gutter plots. Here a pair that differs in alpha alone is rendered in
   each mode, and the gutter must plot nothing — beside a control pair that
   differs in colour, so a gutter that never drew could not pass. The same
   holds for the fifth gutter-tolerance call, in `renderComparison`, whose map
   is not drawn but decides where `n` jumps — so it is asserted through `n`.
"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fixture_server import BASE_REF, png_bytes  # noqa: E402
from spa_harness import active_path, wait_until  # noqa: E402

MODES = ["side-by-side", "crossfade", "swipe", "diff"]

# Same colour, opaque against two-thirds opacity. The colour survives
# premultiplication to within a unit, far inside the gutter's threshold of 10,
# so the only difference the gutter could see is the alpha channel.
ALPHA_ONLY = "alpha_only.png"
COLOUR = "colour.png"


def base_panel_text(page) -> str:
    return page.text_content(".side-by-side .side-panel:first-child .empty-msg") or ""


def test_an_added_file_says_it_has_no_base(spa_backend, spa_page):
    backend = spa_backend(files=[{"path": "new.png", "status": "added"}])
    page = spa_page()
    page.goto(backend.pr_url(), wait_until="load")
    wait_until(lambda: page.query_selector('.side-by-side img[data-role="head"]'),
               "the head side to render")

    assert base_panel_text(page) == "No base image (new file)"
    assert not [r for r in backend.probe.images("new.png") if r["ref"] == BASE_REF], \
        "an added file has no base to request"


def test_a_rename_whose_base_failed_does_not_claim_to_be_new(spa_backend, spa_page):
    """The base fetch failing is a fact about the fetch, not about the diff.

    Swapping the two arms of the ternary — the inversion of #28's fix — fails
    this and the added-file test together.
    """
    backend = spa_backend(
        files=[{"path": "moved/to.png", "status": "renamed",
                "previous_filename": "was/at.png"}],
        images={("was/at.png", "base"): 404})
    page = spa_page()
    page.goto(backend.pr_url(), wait_until="load")
    wait_until(lambda: page.query_selector('.side-by-side img[data-role="head"]'),
               "the head side to render")

    failed = [r for r in backend.probe.images("was/at.png") if r["ref"] == BASE_REF]
    assert failed and all(r.get("status") == 404 for r in failed), failed
    assert base_panel_text(page) == "Base image not available"
    # Kept the modes rather than collapsing to the single-image rename view,
    # and said it was a rename above them.
    assert page.is_visible("#mode-toolbar")
    assert "was/at.png" in (page.text_content(".renamed-label") or "")


# Rows of the gutter canvas painted in the diff colour. The canvas is one
# pixel per image row, so this is a count of rows the gutter calls changed.
GUTTER_RED_ROWS = """() => {
    const c = document.querySelector('#viewport canvas[title="Row diff indicator"]');
    if (!c) return null;
    const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
    let rows = 0;
    for (let y = 0; y < c.height; y++) {
        const i = y * c.width * 4;
        if (d[i] > 200 && d[i + 1] < 100 && d[i + 2] < 100 && d[i + 3] > 200) rows++;
    }
    return rows;
}"""


def show(page, path: str, mode: str) -> int:
    """Select `path` in `mode` and return the gutter's changed-row count."""
    page.click(f'.mode-btn[data-mode="{mode}"]')
    page.click(f'.file-item[data-path="{path}"]')
    wait_until(lambda: active_path(page) == path, f"{path} to be selected")
    # The gutter is built on a timer or an image load after the mode renders,
    # and rebuilt from scratch each time, so wait for one that exists.
    wait_until(lambda: page.evaluate(GUTTER_RED_ROWS) is not None,
               f"the {mode} gutter for {path}")
    return page.evaluate(GUTTER_RED_ROWS)


@pytest.fixture
def alpha_pair(spa_backend, spa_page):
    backend = spa_backend(
        files=[{"path": COLOUR, "status": "modified"},
               {"path": ALPHA_ONLY, "status": "modified"}],
        images={(ALPHA_ONLY, "base"): png_bytes(9, alpha=255),
                (ALPHA_ONLY, "head"): png_bytes(9, alpha=170)})
    page = spa_page()
    page.goto(backend.pr_url(), wait_until="load")
    wait_until(lambda: active_path(page) is not None, "a file to be selected")
    return page


@pytest.mark.parametrize("mode", MODES)
def test_the_gutter_plots_colour_changes_in_every_mode(alpha_pair, mode):
    """The control: in this mode the gutter is drawn, and draws a colour change.

    Without it the alpha test below would pass against a mode whose gutter was
    never built at all.
    """
    assert show(alpha_pair, COLOUR, mode) == 64


@pytest.mark.parametrize("mode", MODES)
def test_the_gutter_ignores_an_alpha_only_change_in_every_mode(alpha_pair, mode):
    """A gutter call that passed `includeAlpha` would plot all 64 rows here."""
    assert show(alpha_pair, ALPHA_ONLY, mode) == 0


def test_the_alpha_only_pair_really_differs(alpha_pair):
    """The diff overlay compares alpha on purpose, so it sees what the gutter must not.

    Without this the zero above could come from a fixture whose two sides were
    identical, and the alpha test would be vacuous.
    """
    show(alpha_pair, ALPHA_ONLY, "diff")
    stats = re.search(r"([\d,]+) changed pixels \(",
                      alpha_pair.text_content("#viewport") or "")
    assert stats and stats.group(1) == "4,096", stats


# Taller than the viewport, so a change cluster spanning the image has its
# middle below the viewport's centre and `n` has somewhere to scroll to.
TALL_ALPHA, TALL_COLOUR, AFTER = "tall_alpha.png", "tall_colour.png", "after.png"


@pytest.fixture
def tall_files(spa_backend, spa_page):
    backend = spa_backend(
        files=[{"path": p, "status": "modified"} for p in (TALL_COLOUR, TALL_ALPHA, AFTER)],
        images={(TALL_ALPHA, "base"): png_bytes(9, size=16, height=2000, alpha=255),
                (TALL_ALPHA, "head"): png_bytes(9, size=16, height=2000, alpha=170),
                (TALL_COLOUR, "base"): png_bytes(9, size=16, height=2000),
                (TALL_COLOUR, "head"): png_bytes(30, size=16, height=2000)})
    page = spa_page()
    page.goto(backend.pr_url(), wait_until="load")
    return page


def press_n_on(page, path: str) -> None:
    page.click(f'.file-item[data-path="{path}"]')
    wait_until(lambda: page.evaluate("""() => {
        const img = document.querySelector('#viewport img[data-role="head"]');
        return img && img.complete && img.naturalHeight === 2000;
    }"""), f"{path} to render")
    page.keyboard.press("n")


def test_n_scrolls_to_a_colour_change_within_the_file(tall_files):
    """The control: a change cluster below the centre is somewhere `n` goes."""
    press_n_on(tall_files, TALL_COLOUR)
    wait_until(lambda: tall_files.evaluate(
        "document.getElementById('viewport').scrollTop") > 100,
        "n to scroll down to the change")
    assert active_path(tall_files) == TALL_COLOUR


def test_n_does_not_stop_at_an_alpha_only_change(tall_files):
    """An alpha-only file has no clusters, so `n` moves on to the next file.

    Kills `includeAlpha` passed at the `renderComparison` call, which builds a
    cluster over the whole image and has `n` scroll into it instead.
    """
    press_n_on(tall_files, TALL_ALPHA)
    wait_until(lambda: active_path(tall_files) == AFTER, "n to move to the next file")
