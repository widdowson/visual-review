"""Browser-driven tests for the sidebar's two-line file label (#55).

#47 gave a row whose basename is shared a second line naming the directory
that tells it apart. //:test_file_label runs the decision (`dirLabelFor`) and
the render against stubs, and pins the stylesheet by grepping it — which
catches a deleted declaration and says nothing about what the browser lays
out. This reads the layout itself.

The payload is #47's manual run: a three-way basename collision, a root
against `docs/` collision and one uncollided file. Two rows are added, a
collision between two deep directories under a long basename, because none
of the six is wide enough to overflow the column and so none can show that
the column holds.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from spa_harness import active_path, wait_until  # noqa: E402

# Hyphenated, because a hyphen is a line-break opportunity and an underscore is
# not: without `white-space: nowrap` these lines would wrap, where an
# unbreakable name would simply overflow and hide the declaration's absence.
LONG_NAME = "an-unusually-long-baseline-filename-that-cannot-fit.bmp"
LONG_DIRS = ("an-unreasonably-long-directory-for-the-first-variant",
             "an-unreasonably-long-directory-for-the-second-variant")

PATHS = [
    "django/apps/home/tests/visual/cuj_01_login.bmp",
    "django/apps/shoots/tests/visual/cuj_01_login.bmp",
    "django/apps/proofing/tests/visual/cuj_01_login.bmp",
    "README.bmp",
    "docs/README.bmp",
    "django/apps/home/tests/visual/home_page.bmp",
] + [f"screens/{d}/{LONG_NAME}" for d in LONG_DIRS]

# What each row's second line says, None for a row that has none. The three
# shapes #47 distinguishes: a whole directory as itself, a tail of one behind
# an ellipsis, and the repository root as `./`.
EXPECTED_DIR_LINE = {
    PATHS[0]: "…/home/tests/visual/",
    PATHS[1]: "…/shoots/tests/visual/",
    PATHS[2]: "…/proofing/tests/visual/",
    PATHS[3]: "./",
    PATHS[4]: "docs/",
    PATHS[5]: None,
    PATHS[6]: f"…/{LONG_DIRS[0]}/",
    PATHS[7]: f"…/{LONG_DIRS[1]}/",
}

# Every geometric fact the assertions need, read in one pass so they all
# describe the same layout.
MEASURE = """() => {
    const box = el => el && el.getBoundingClientRect();
    const list = document.getElementById('file-list');
    return {
        list: {clientWidth: list.clientWidth, scrollWidth: list.scrollWidth},
        rows: [...document.querySelectorAll('.file-item')].map(li => {
            const name = li.querySelector('.filename');
            const dir = li.querySelector('.file-dir');
            const badge = li.querySelector('.comment-badge');
            const lineHeight = el => parseFloat(getComputedStyle(el).lineHeight)
                || parseFloat(getComputedStyle(el).fontSize) * 1.5;
            const one = el => el && {
                text: el.textContent, top: box(el).top, bottom: box(el).bottom,
                left: box(el).left, right: box(el).right, height: box(el).height,
                lineHeight: lineHeight(el),
                clientWidth: el.clientWidth, scrollWidth: el.scrollWidth,
            };
            const style = getComputedStyle(li);
            return {
                path: li.getAttribute('data-path'),
                contentRight: box(li).right - parseFloat(style.paddingRight),
                clientWidth: li.clientWidth, scrollWidth: li.scrollWidth,
                right: box(li).right,
                name: one(name), dir: one(dir), badge: one(badge),
            };
        }),
    };
}"""


def open_sidebar(spa_backend, spa_page):
    # A comment on the short root-level file, so one row carries a badge on a
    # row whose label is narrow enough that the badge could slide in beside it.
    backend = spa_backend(
        files=[{"path": p, "status": "modified"} for p in PATHS],
        comments={"README.bmp": [{"user": "r", "body": "x",
                                  "created_at": "2026-10-01T12:00:00Z"}]})
    page = spa_page()
    page.goto(backend.pr_url(), wait_until="load")
    wait_until(lambda: active_path(page) is not None, "a file to be selected")
    wait_until(lambda: page.query_selector(
        '.file-item[data-path="README.bmp"] .comment-badge'), "the comment badge")
    return page, page.evaluate(MEASURE)


def test_each_row_says_what_dirLabelFor_says(spa_backend, spa_page):
    """The three label shapes reach the DOM as text, and a lone basename gets none."""
    _, m = open_sidebar(spa_backend, spa_page)
    by_path = {r["path"]: r for r in m["rows"]}
    assert set(by_path) == set(PATHS)
    for path, want in EXPECTED_DIR_LINE.items():
        row = by_path[path]
        assert row["name"]["text"] == path.rsplit("/", 1)[-1], row
        assert (row["dir"] and row["dir"]["text"]) == want, (path, row["dir"])


def test_the_directory_renders_on_its_own_line_under_the_filename(spa_backend, spa_page):
    """What `display: flex` and `flex-direction: column` on the label buy.

    Without either, the two spans share a line and the directory sits beside
    the filename. Kills removing either declaration.
    """
    _, m = open_sidebar(spa_backend, spa_page)
    rows = [r for r in m["rows"] if r["dir"]]
    assert rows
    for r in rows:
        assert r["dir"]["top"] >= r["name"]["bottom"] - 0.5, r
        assert abs(r["dir"]["left"] - r["name"]["left"]) < 0.5, r


def test_a_deep_path_does_not_widen_the_row_or_wrap(spa_backend, spa_page):
    """What `min-width: 0`, `overflow: hidden` and `white-space: nowrap` buy together.

    Each label line stays one line tall, nothing in a row reaches past the
    sidebar, and the long lines are clipped inside their own boxes rather than
    spilling over the badge. Which of the three declarations is missing changes
    which of these goes red, not whether one does.
    """
    _, m = open_sidebar(spa_backend, spa_page)
    assert m["list"]["scrollWidth"] <= m["list"]["clientWidth"], m["list"]
    long_rows = [r for r in m["rows"] if LONG_NAME in r["path"]]
    assert len(long_rows) == 2
    for r in m["rows"]:
        assert r["scrollWidth"] <= r["clientWidth"], (r["path"], r["scrollWidth"], r["clientWidth"])
        for part in (r["name"], r["dir"]):
            if not part:
                continue
            # One line: a wrapped line is at least two line-heights tall.
            assert part["height"] < 1.5 * part["lineHeight"], (r["path"], part)
            edge = r["badge"]["left"] if r["badge"] else r["contentRight"]
            assert part["right"] <= edge + 0.5, (r["path"], part)
    for r in long_rows:
        # Clipped, not merely short: the text really is longer than its box,
        # so the two checks above are about a line that had to give way.
        assert r["name"]["scrollWidth"] > r["name"]["clientWidth"], r["name"]
        assert r["dir"]["scrollWidth"] > r["dir"]["clientWidth"], r["dir"]


def test_the_comment_badge_sits_at_the_right_edge(spa_backend, spa_page):
    """What `flex: 1` on the label buys: the label takes the slack, not the badge.

    Without it the label shrinks to its text and the badge sits beside a short
    filename halfway across the row.
    """
    _, m = open_sidebar(spa_backend, spa_page)
    row = next(r for r in m["rows"] if r["path"] == "README.bmp")
    assert row["badge"], row
    assert abs(row["badge"]["right"] - row["contentRight"]) < 1, row
