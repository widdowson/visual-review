"""Browser-driven tests for the comments pane's stale-response guard (#53).

//:test_comments_freshness checks the guard two ways: the decision is a pure
function it extracts and runs, and the fetch around it gets structural checks
asserting nothing in `loadComments` touches the pane outside the block that
owns it. That second half can only see what `bodyOf('loadComments')` can see,
so a write from a helper defined outside it is invisible to it.

These tests do not care where the write lives. They hold responses open in the
fixture to make the interleavings happen by construction, then read the pane:
after switching files faster than the comments API answers, the pane shows the
selected file's comments, and is not blank.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from spa_harness import active_path, wait_until  # noqa: E402

A, B = "shots/a.png", "shots/b.png"

# Counts, per path, the comments GETs whose answer the page has finished
# handling. The fixture closing its record says the bytes went out, not that
# the page has acted on them, and the assertions here are about what the page
# did with a late answer — so they need the later of the two.
#
# Microtask ordering rather than time, as in test_spa_signed_in_chip.py's
# DECISION_MARKER: this wrapper registers on the fetch promise before the SPA
# can, so at each hop its continuation is queued first, and the extra
# Promise.resolve() hop puts the count strictly after the SPA's handler. A
# rejection is counted too, since the SPA's catch renders as well.
COMMENTS_SETTLED = """
window.__vrCommentsSettled = {};
(function() {
  var native = window.fetch;
  window.fetch = function(input, init) {
    var p = native.call(this, input, init);
    var url = String(input);
    var isGet = !init || !init.method || init.method === 'GET';
    var m = isGet && url.match(/\\/comments\\?path=([^&]*)/);
    if (m) {
      var path = decodeURIComponent(m[1]);
      var settle = function() {
        window.__vrCommentsSettled[path] = (window.__vrCommentsSettled[path] || 0) + 1;
      };
      p.then(function(r) { return r.clone().json(); })
       .then(function() { return Promise.resolve(); })
       .then(settle, settle);
    }
    return p;
  };
})();
"""


def handled(page, path: str) -> int:
    return page.evaluate("p => window.__vrCommentsSettled[p] || 0", path)


def comment(body: str) -> dict:
    return {"user": "reviewer", "body": body,
            "created_at": "2026-10-01T12:00:00Z",
            "html_url": "https://example.invalid/c"}


def pane_text(page) -> str:
    return page.text_content("#comments-scroll") or ""


def open_on_a(spa_backend, spa_page, **holds):
    backend = spa_backend(
        files=[{"path": A, "status": "modified"}, {"path": B, "status": "modified"}],
        comments={A: [comment("only on A")], B: [comment("only on B")]})
    held = {kind: backend.state.hold(kind, A) for kind in holds}
    page = spa_page(COMMENTS_SETTLED)
    page.goto(backend.pr_url(), wait_until="load")
    wait_until(lambda: active_path(page) == A, "file A to be selected")
    return backend, page, held


def assert_pane_shows_b(page) -> None:
    text = pane_text(page)
    assert "only on B" in text, f"the pane should hold B's thread, not {text!r}"
    assert "only on A" not in text, text
    assert "Comments on this file (1)" in text, text


def test_a_late_answer_for_the_file_left_behind_is_dropped(spa_backend, spa_page):
    """Select A, then B before A's comments arrive: B's thread stays.

    Kills dropping the `isCurrent()` check before `renderComments`, which
    paints A's thread over B's when A's answer lands last.
    """
    backend, page, held = open_on_a(spa_backend, spa_page, comments=True)
    wait_until(held["comments"].reached.is_set, "A's comments request to be held")

    page.click(f'.file-item[data-path="{B}"]')
    wait_until(lambda: "only on B" in pane_text(page), "B's thread to render")

    held["comments"].release()
    wait_until(lambda: handled(page, A) == 1, "the page to handle A's late answer")
    assert_pane_shows_b(page)


def test_posting_on_a_then_leaving_does_not_touch_bs_pane(spa_backend, spa_page):
    """Post on A, switch to B before the POST answers: B's pane survives the reload.

    The reload starts once the POST lands, when A is no longer on screen, so
    it does not own the pane. This is the case #53's mutation is about: a
    non-owning load that blanks the pane from a helper outside `loadComments`
    leaves //:test_comments_freshness green and fails here.
    """
    backend, page, held = open_on_a(spa_backend, spa_page, comment_post=True)
    wait_until(lambda: "only on A" in pane_text(page), "A's thread to render")

    page.fill("#comment-text", "posted on A")
    page.click("#comment-submit")
    wait_until(held["comment_post"].reached.is_set, "the POST to be held")
    page.click(f'.file-item[data-path="{B}"]')
    wait_until(lambda: "only on B" in pane_text(page), "B's thread to render")

    before = handled(page, A)
    held["comment_post"].release()
    wait_until(lambda: handled(page, A) > before,
               "the page to handle the reload of A's comments after the POST")
    # Still written: the badge is outside the pane's guard on purpose, so the
    # count for a comment just posted is not lost by navigating away.
    wait_until(lambda: (page.text_content(f'.file-item[data-path="{A}"] .comment-badge')
                        or "").endswith("2"), "A's badge to count the new comment")
    assert_pane_shows_b(page)


def test_a_reload_already_running_when_the_user_leaves_is_dropped(spa_backend, spa_page):
    """The other order: the reload starts while A is on screen, then the user leaves.

    That reload does own the pane when it starts, and writes its spinner; B's
    load then takes the pane over, and the reload's answer must not take it
    back.
    """
    backend, page, _ = open_on_a(spa_backend, spa_page)
    wait_until(lambda: "only on A" in pane_text(page), "A's thread to render")
    reload_hold = backend.state.hold("comments", A)

    page.fill("#comment-text", "posted on A")
    page.click("#comment-submit")
    wait_until(reload_hold.reached.is_set, "the post's reload of A to be held")
    page.click(f'.file-item[data-path="{B}"]')
    wait_until(lambda: "only on B" in pane_text(page), "B's thread to render")

    before = handled(page, A)
    reload_hold.release()
    wait_until(lambda: handled(page, A) > before, "the page to handle the held reload")
    assert_pane_shows_b(page)

    # And A has the comment when the user goes back to it.
    page.click(f'.file-item[data-path="{A}"]')
    wait_until(lambda: "posted on A" in pane_text(page), "A's thread to include the post")
