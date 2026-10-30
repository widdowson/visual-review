"""Browser-driven tests for the comments pane's layout and drag handle (#42).

//:test_comments_pane runs the pane's two pure functions and checks the
DOM-touching half by matching source text. #42 lists six gaps that approach
left open, each confirmed by mutation with that target green. A regex can pin
the names in the source but not the relations between them, so these read the
rendered pane instead:

1. A later rule overriding the pane's stylesheet — `max-height: none`,
   `display: block`, `flex-direction: row`, `overflow-y: hidden` or `flex: none`
   — leaves the source-text checks green. Each breaks one of the layout facts
   below.
2. An unquoted `url()` holding a `}` desynchronised that file's brace walk.
   The browser's own CSS parser is what reads the stylesheet here.
3. The drag listener moved from the handle to `document` arms the drag on
   every mousedown on the page.
4. `.comment-card` renamed in the template and the CSS makes the pane open on
   the oldest comment.
6. A mistyped `getElementById` id string. Either one throws, which
   `spa_page` fails on, and leaves the pane unrendered or undraggable.

Item 5, `assertPasses` escaping five metacharacters of ten, is a defect in that
file's own helper rather than in the page, and is fixed there.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from spa_harness import active_path, wait_until  # noqa: E402

PATH = "shots/a.png"
COUNT = 14


def comments(n: int) -> list[dict]:
    return [{"user": "reviewer", "body": f"comment number {i:02d}",
             "created_at": "2026-10-01T12:00:00Z",
             "html_url": "https://example.invalid/c"} for i in range(1, n + 1)]


GEOMETRY = """() => {
    const r = el => { const b = el.getBoundingClientRect();
                      return {top: b.top, bottom: b.bottom, left: b.left,
                              right: b.right, width: b.width, height: b.height}; };
    const scroll = document.getElementById('comments-scroll');
    const body = text => [...scroll.querySelectorAll('*')]
        .find(el => el.children.length === 0 && el.textContent === text);
    return {
        innerHeight: window.innerHeight,
        section: r(document.getElementById('comments-section')),
        handle: r(document.getElementById('comments-resize')),
        scroll: Object.assign(r(scroll), {
            scrollTop: scroll.scrollTop, scrollHeight: scroll.scrollHeight,
            clientHeight: scroll.clientHeight}),
        newest: r(body('comment number %02d')),
        oldest: r(body('comment number 01')),
    };
}""" % COUNT


def open_pane(spa_backend, spa_page):
    backend = spa_backend(files=[{"path": PATH, "status": "modified"}],
                          comments={PATH: comments(COUNT)})
    page = spa_page()
    page.goto(backend.pr_url(), wait_until="load")
    wait_until(lambda: active_path(page) == PATH, "the file to be selected")
    wait_until(lambda: f"Comments on this file ({COUNT})"
               in (page.text_content("#comments-scroll") or ""), "the thread to render")
    return page


def geometry(page) -> dict:
    return page.evaluate(GEOMETRY)


def test_the_pane_is_capped_and_its_body_scrolls_inside_it(spa_backend, spa_page):
    """The pane opens at most 30vh tall, and the comments scroll within it.

    Kills each override in #42's table: `max-height: none` uncaps the pane;
    `display: block` or `flex: none` lets the body spill below the pane at its
    full height, so it no longer scrolls.
    """
    g = geometry(open_pane(spa_backend, spa_page))
    assert g["section"]["height"] <= 0.30 * g["innerHeight"] + 1, g["section"]
    assert g["scroll"]["scrollHeight"] > g["scroll"]["clientHeight"], g["scroll"]
    assert g["scroll"]["bottom"] <= g["section"]["bottom"] + 0.5, g
    assert g["scroll"]["top"] >= g["section"]["top"], g


def test_the_handle_lies_across_the_top_of_the_pane(spa_backend, spa_page):
    """A flex column: the handle spans the pane's width, above the body.

    Kills `flex-direction: row`, which lays the handle out beside the body at
    zero width.
    """
    g = geometry(open_pane(spa_backend, spa_page))
    assert g["handle"]["height"] > 0, g["handle"]
    assert abs(g["handle"]["width"] - g["scroll"]["width"]) < 1, g
    assert g["handle"]["bottom"] <= g["scroll"]["top"] + 0.5, g


def test_the_pane_opens_on_the_newest_comment(spa_backend, spa_page):
    """The newest comment is in view and the oldest has been scrolled past.

    Kills renaming `.comment-card` in both the template and the CSS: the
    comments are found here by their text, so the rename leaves this test able
    to see them and the pane opening on the oldest.
    """
    g = geometry(open_pane(spa_backend, spa_page))
    assert g["scroll"]["top"] - 0.5 <= g["newest"]["top"], g
    assert g["newest"]["bottom"] <= g["scroll"]["bottom"] + 0.5, g
    assert g["oldest"]["bottom"] <= g["scroll"]["top"], g


def test_the_wheel_scrolls_the_comments(spa_backend, spa_page):
    """A reader can scroll back through the thread.

    Kills `overflow-y: hidden` on the body, under which the scripted scroll to
    the newest comment still works and only a person's scrolling does not.
    """
    page = open_pane(spa_backend, spa_page)
    g = geometry(page)
    page.mouse.move(g["scroll"]["left"] + 40, g["scroll"]["top"] + 40)
    page.mouse.wheel(0, -200)
    wait_until(lambda: geometry(page)["scroll"]["scrollTop"] < g["scroll"]["scrollTop"],
               "the wheel to scroll the thread back")


def test_dragging_the_handle_resizes_the_pane(spa_backend, spa_page):
    """The drag sets the pane's height to where the pointer is.

    `computeCommentsPaneHeight` gives `paneBottom - pointerY` inside its clamps,
    and a 120px drag at 1280x900 is well inside both.
    """
    page = open_pane(spa_backend, spa_page)
    g = geometry(page)
    x = g["handle"]["left"] + g["handle"]["width"] / 2
    y = g["handle"]["top"] + g["handle"]["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x, y - 120, steps=4)
    page.mouse.up()

    after = geometry(page)
    assert abs(after["section"]["bottom"] - g["section"]["bottom"]) < 1, after
    assert abs(after["section"]["height"] - (g["section"]["bottom"] - (y - 120))) < 1.5, \
        (g["section"], after["section"])


def test_a_drag_starts_only_on_the_handle(spa_backend, spa_page):
    """A mousedown anywhere else neither resizes the pane nor blocks the page.

    Kills moving the mousedown listener from the handle to `document`. That
    arms the drag on every press, and its `preventDefault` stops a press from
    focusing a textarea — the comment box included.
    """
    page = open_pane(spa_backend, spa_page)
    box = page.eval_on_selector("#comment-text", """el => {
        el.scrollIntoView({block: 'nearest'});
        const b = el.getBoundingClientRect();
        return {x: b.left + 20, y: b.top + 10};
    }""")
    g = geometry(page)
    page.mouse.move(box["x"], box["y"])
    page.mouse.down()
    page.mouse.move(box["x"], box["y"] - 150, steps=4)
    page.mouse.up()

    after = geometry(page)
    assert abs(after["section"]["height"] - g["section"]["height"]) < 1, (g, after)
    assert page.evaluate("document.activeElement && document.activeElement.id") == \
        "comment-text", "a press on the comment box must focus it"
