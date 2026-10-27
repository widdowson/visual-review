"""The harness's own knobs, each driven through the real page.

Every other browser test asserts something about the SPA and trusts the
fixture to have served what it was told to. This file is where that trust is
checked, one knob at a time, so that a fixture that quietly ignored `files`
or `comments` fails here by name rather than as a confusing red in whichever
test first relied on it. It is also the smallest worked example of the three
shared fixtures in conftest.py.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fixture_server import FixtureState  # noqa: E402
from spa_harness import active_path, rendered_image_count, wait_until  # noqa: E402

PATHS = ["docs/a.png", "app/home/tests/visual/a.png", "b.png"]


def comment(body: str) -> dict:
    return {"user": "reviewer", "body": body,
            "created_at": "2026-10-01T12:00:00Z",
            "html_url": "https://example.invalid/c"}


def sidebar_paths(page) -> list[str]:
    return page.eval_on_selector_all(
        ".file-item", "els => els.map(e => e.getAttribute('data-path'))")


def pane_text(page) -> str:
    # text_content, not inner_text: the heading is styled uppercase, and
    # inner_text reports text as rendered.
    return page.text_content("#comments-scroll") or ""


def test_files_replaces_the_generated_list(spa_backend, spa_page):
    """What a test passes as `files` is exactly what the sidebar lists."""
    backend = spa_backend(files=[{"path": p, "status": "modified"} for p in PATHS])
    page = spa_page()
    page.goto(backend.pr_url(), wait_until="load")
    wait_until(lambda: active_path(page) is not None, "a file to be selected")

    assert sorted(sidebar_paths(page)) == sorted(PATHS)
    wait_until(lambda: rendered_image_count(page) == 2, "the first pair to render")


def test_comments_are_served_per_path(spa_backend, spa_page):
    """Each file's pane shows its own thread, and only its own."""
    backend = spa_backend(
        files=[{"path": p, "status": "modified"} for p in PATHS[:2]],
        comments={PATHS[0]: [comment("on the docs file")]})
    page = spa_page()
    page.goto(backend.pr_url("#" + PATHS[0]), wait_until="load")
    wait_until(lambda: active_path(page) == PATHS[0], "the deep-linked file")
    wait_until(lambda: "on the docs file" in pane_text(page), "its comment to render")

    page.click(f'.file-item[data-path="{PATHS[1]}"]')
    wait_until(lambda: "Comments on this file (0)" in pane_text(page),
               "the other file's empty thread to render")
    assert "on the docs file" not in pane_text(page)


def test_a_held_comments_response_waits_for_release(spa_backend, spa_page):
    """Nothing is sent while a hold matches, and everything is once released.

    The positive half matters as much as the negative: a hold that never let
    go would pass the first assertion and fail every test built on it.
    """
    backend = spa_backend(file_count=1,
                          comments={"shots/file_00.png": [comment("held back")]})
    hold = backend.state.hold("comments", "shots/file_00.png")
    page = spa_page()
    page.goto(backend.pr_url(), wait_until="load")

    wait_until(hold.reached.is_set, "the comments request to arrive and be held")
    wait_until(lambda: rendered_image_count(page) == 2, "the images, which are not held")
    assert "held back" not in pane_text(page)
    assert page.query_selector("#comments-scroll .spinner") is not None, \
        "the pane should still be waiting on the held response"

    hold.release()
    wait_until(lambda: "held back" in pane_text(page), "the released comment to render")


def test_a_held_image_the_page_abandons_is_recorded_as_aborted(spa_backend, spa_page):
    """The browser walking away from a held request closes it as an abort.

    Without this, a test would have to release a hold before it could see
    whether the page had cancelled it, and the release would race the cancel.
    """
    backend = spa_backend(file_count=6)
    hold = backend.state.hold("image", "shots/file_03.png")
    page = spa_page()
    page.goto(backend.pr_url("#file_03.png"), wait_until="load")
    wait_until(hold.reached.is_set, "file 3 to be requested and held")

    page.click('.file-item[data-path="shots/file_00.png"]')
    aborted = wait_until(
        lambda: [r for r in backend.probe.images("file_03") if r["aborted"]],
        "the held file-3 requests to be closed as aborted")
    # Nothing in this test releases the hold, so these records were closed by
    # the browser walking away, not by a release racing it.
    assert all(r["held"] for r in aborted), aborted


def test_the_knobs_refuse_what_they_cannot_honour():
    """Two mistakes that would otherwise be silently ignored."""
    with pytest.raises(ValueError, match="renamed_index"):
        FixtureState(files=[{"path": "a.png", "status": "modified"}], renamed_index=0)
    with pytest.raises(ValueError, match="hold"):
        FixtureState().hold("checks", "a.png")
    with pytest.raises(ValueError, match="ref"):
        FixtureState().hold("comments", "a.png", ref="b" * 40)


def test_an_expired_hold_closes_as_aborted_and_is_recorded(spa_backend, spa_page,
                                                         monkeypatch):
    """A forgotten hold is a failure of the test, not a delivered transfer.

    The record closes as aborted, so a predicate like `end is not None and not
    aborted` cannot mistake it for an image that arrived, and the expiry is
    recorded where spa_backend's teardown fails the test on it. The list is
    emptied here, after checking it, because expiring is this test's point.
    """
    import fixture_server
    monkeypatch.setattr(fixture_server, "HOLD_TIMEOUT", 0.5)
    backend = spa_backend(file_count=1)
    backend.state.hold("image", "shots/file_00.png", ref="b" * 40)
    page = spa_page()
    page.goto(backend.pr_url(), wait_until="commit")

    expired = wait_until(
        lambda: [r for r in backend.probe.images("file_00") if r["timed_out"]],
        "the held head to expire")
    assert all(r["aborted"] and r["end"] is not None for r in expired), expired
    assert backend.state.expired_holds, "the expiry should be recorded for teardown"
    backend.state.expired_holds.clear()
