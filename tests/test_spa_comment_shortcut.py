"""Cmd-Enter (Ctrl-Enter off a Mac) in the comment box posts it (#65).

Every negative case here is proved by ordering, not by waiting: after the
keystroke that must not post, the test posts once on purpose and then asserts
the fixture saw exactly that one POST. A post the keystroke had wrongly sent
was issued first, so it is in the log by the time the deliberate one lands.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from spa_harness import wait_until  # noqa: E402

PATH = "shots/file_00.png"


def pane_text(page) -> str:
    return page.text_content("#comments-scroll") or ""


def open_pane(spa_backend, spa_page, hold_post: bool = False):
    backend = spa_backend(file_count=1)
    hold = backend.state.hold("comment_post", PATH) if hold_post else None
    page = spa_page()
    page.goto(backend.pr_url(), wait_until="load")
    wait_until(lambda: "Comments on this file (0)" in pane_text(page), "the empty thread")
    return backend, page, hold


def posts(backend) -> list[tuple[str, str]]:
    return [(r["path"], r["body"]) for r in backend.probe.log()
            if r["kind"] == "comment_post"]


def post_on_purpose(page, body: str) -> None:
    page.fill("#comment-text", body)
    page.click("#comment-submit")
    wait_until(lambda: body in pane_text(page), "the deliberate post to be read back")


@pytest.mark.parametrize("chord", ["Meta+Enter", "Control+Enter"])
def test_the_chord_posts_the_comment(spa_backend, spa_page, chord):
    backend, page, _ = open_pane(spa_backend, spa_page)
    page.fill("#comment-text", "posted from the keyboard")
    page.press("#comment-text", chord)
    wait_until(lambda: "posted from the keyboard" in pane_text(page),
               "the comment to be posted and read back")
    assert posts(backend) == [(PATH, "posted from the keyboard")]
    assert page.input_value("#comment-text") == "", "the box should clear after posting"


# Not Alt+Enter: Chromium inserts nothing for it on Linux, page or no page.
@pytest.mark.parametrize("key", ["Enter", "Shift+Enter"])
def test_enter_without_the_modifier_is_a_newline(spa_backend, spa_page, key):
    backend, page, _ = open_pane(spa_backend, spa_page)
    page.fill("#comment-text", "line one")
    page.press("#comment-text", key)
    page.type("#comment-text", "line two")
    assert page.input_value("#comment-text") == "line one\nline two"
    page.click("#comment-submit")
    wait_until(lambda: "line two" in pane_text(page), "the deliberate post to be read back")
    assert posts(backend) == [(PATH, "line one\nline two")]


def test_an_empty_box_posts_nothing(spa_backend, spa_page):
    backend, page, _ = open_pane(spa_backend, spa_page)
    page.fill("#comment-text", "   ")
    page.press("#comment-text", "Meta+Enter")
    post_on_purpose(page, "the only post")
    assert posts(backend) == [(PATH, "the only post")]


def test_a_second_press_while_posting_does_not_post_twice(spa_backend, spa_page):
    backend, page, hold = open_pane(spa_backend, spa_page, hold_post=True)
    page.fill("#comment-text", "pressed twice")
    page.press("#comment-text", "Meta+Enter")
    wait_until(hold.reached.is_set, "the first POST to arrive and be held")
    assert page.is_disabled("#comment-submit")
    page.press("#comment-text", "Meta+Enter")
    hold.release()
    wait_until(lambda: "pressed twice" in pane_text(page), "the post to land once released")
    assert posts(backend) == [(PATH, "pressed twice")]


# Playwright's keyboard cannot mark a key as part of an IME composition, so this
# dispatches the event itself and reads the verdict in the same evaluate: the
# click handler disables the button synchronously, so a post shows there before
# the pane can re-render. The second dispatch, identical bar isComposing, is the
# control showing a synthetic event reaches the handler at all.
DISPATCH = """composing => {
    document.getElementById('comment-text').dispatchEvent(
        new KeyboardEvent('keydown', {key: 'Enter', metaKey: true, isComposing: composing,
                                      bubbles: true, cancelable: true}));
    return document.getElementById('comment-submit').disabled;
}"""


def test_a_keystroke_an_ime_is_composing_does_not_post(spa_backend, spa_page):
    backend, page, _ = open_pane(spa_backend, spa_page)
    page.fill("#comment-text", "\u304b\u306a")
    assert page.evaluate(DISPATCH, True) is False, "a composing keystroke started a post"
    assert page.evaluate(DISPATCH, False) is True, "the control dispatch did not post"
    wait_until(lambda: "\u304b\u306a" in pane_text(page), "the control post to be read back")
    assert posts(backend) == [(PATH, "\u304b\u306a")]
