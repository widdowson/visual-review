"""The PR page's top bar, laid out by a real browser at phone and desktop widths.

On a phone the bar used to keep everything on one row: the title got whatever
width the controls left it and wrapped a word a line, and the signed-in chip,
the theme toggle and the shortcuts button were pushed off the right edge. On a
phone it is now two rows -- hamburger and controls, then the title across the
whole width with its links wrapping under it -- and on a desktop it is
unchanged, one row. These read the boxes rather than the stylesheet, since the
failure is a geometry one.
"""

import pytest

EMAIL = "andrew.widdowson@apw.photos"
# The title from the report that prompted this: long enough to wrap anywhere.
LONG_TITLE = ("Scope home page and shoot prev/next to the viewer's shoots "
              "(#1758)")
PHONE = {"width": 375, "height": 812}
DESKTOP = {"width": 1280, "height": 900}

# The LGTM button is drawn for Andrew, so it is laid out here too: the
# widest the controls get is with it present.
CONTROLS = ["#lgtm-btn", "#vr-user", "#theme-toggle", "#kbd-hint"]


@pytest.fixture
def open_pr(spa_backend, spa_page):
    def _open(viewport):
        backend = spa_backend(signed_in_email=EMAIL, pr_title=LONG_TITLE,
                              lgtm={"can_apply": True, "applied": False})
        page = spa_page(viewport=viewport)
        page.goto(backend.url + "/owner/repo/pr/1", wait_until="load")
        page.locator("#vr-user").wait_for(state="visible", timeout=10_000)
        page.locator("#lgtm-btn").wait_for(state="visible", timeout=10_000)
        page.locator("#pr-meta .ci-badge").wait_for(state="visible", timeout=10_000)
        return page

    return _open


def box(page, selector):
    b = page.locator(selector).bounding_box()
    assert b is not None, f"{selector} is not laid out"
    return b


class TestPhone:
    def test_nothing_overflows_the_right_edge(self, open_pr):
        page = open_pr(PHONE)
        for sel in CONTROLS + ["#sidebar-toggle", "#pr-title", "#pr-meta .ci-badge"]:
            b = box(page, sel)
            assert b["x"] >= 0 and b["x"] + b["width"] <= PHONE["width"] + 0.5, (
                f"{sel} spans {b['x']:.1f}..{b['x'] + b['width']:.1f} "
                f"in a {PHONE['width']}px viewport")
        assert page.evaluate("document.documentElement.scrollWidth") <= PHONE["width"]

    def test_controls_share_the_first_row_and_the_title_has_the_second(self, open_pr):
        page = open_pr(PHONE)
        toggle = box(page, "#sidebar-toggle")
        title = box(page, "#pr-title")
        for sel in CONTROLS:
            b = box(page, sel)
            assert b["y"] < title["y"], f"{sel} is not above the title"
            assert abs((b["y"] + b["height"] / 2) - (toggle["y"] + toggle["height"] / 2)) < 4, (
                f"{sel} is not on the hamburger's row")
        # The title takes the whole width under them, not a column beside them.
        assert title["width"] > PHONE["width"] - 40

    def test_the_title_is_not_wrapped_a_word_a_line(self, open_pr):
        page = open_pr(PHONE)
        # One client rect per line box the text was laid out in.
        lines = page.evaluate("""() => {
            const el = document.querySelector('#pr-title .pr-title-text');
            const r = document.createRange();
            r.selectNodeContents(el);
            return new Set([...r.getClientRects()].map(c => Math.round(c.bottom))).size;
        }""")
        assert lines <= 3, f"the title takes {lines} lines"

    def test_the_links_sit_under_the_title(self, open_pr):
        page = open_pr(PHONE)
        text = box(page, "#pr-title .pr-title-text")
        meta = box(page, "#pr-meta")
        assert meta["y"] >= text["y"] + text["height"] - 1
        # Left-aligned with the title, not indented by a margin meant for
        # a gap after inline text.
        link = box(page, "#pr-meta > a:first-child")
        assert abs(link["x"] - text["x"]) < 1, (
            f"the links start at {link['x']:.1f}, the title at {text['x']:.1f}")

    def test_the_layout_fills_the_rest_of_the_screen(self, open_pr):
        page = open_pr(PHONE)
        bar = box(page, ".topbar")
        layout = box(page, ".layout")
        assert abs(layout["y"] - (bar["y"] + bar["height"])) < 1
        assert abs(layout["y"] + layout["height"] - PHONE["height"]) < 1


class TestDesktop:
    def test_one_row(self, open_pr):
        page = open_pr(DESKTOP)
        bar = box(page, ".topbar")
        title = box(page, "#pr-title")
        assert bar["height"] < 60, f"the bar is {bar['height']}px tall"
        for sel in CONTROLS:
            b = box(page, sel)
            assert b["x"] > title["x"] + title["width"], f"{sel} is not right of the title"
            assert b["x"] + b["width"] <= DESKTOP["width"]

    def test_the_links_follow_the_title_on_its_line(self, open_pr):
        page = open_pr(DESKTOP)
        text = box(page, "#pr-title .pr-title-text")
        link = box(page, "#pr-meta > a:first-child")
        assert link["x"] > text["x"] + text["width"] - 1
        assert link["y"] < text["y"] + text["height"]
