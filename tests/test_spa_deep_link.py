"""Browser-driven tests for the two deep-link call sites around the hash (#35).

`selectFileFromHash` and `selectFile` are lifted out of static/index.html and
run against stubs in //:test_hash_target. What that cannot reach is the two
places that call them, and they are the ones a shared link goes through:

- `loadPrImages`, which on a cold load consults the hash and falls back to the
  first file only when the hash names none;
- the `hashchange` listener, which is what makes a hash edited after load, or
  the browser's back button, move the selection.

#35 recorded four edits there that left `bazel test //...` green with deep
linking dead. Each test below names the ones it kills.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from spa_harness import LOAD_COUNTER, active_path, load_starts, wait_until  # noqa: E402

FILE_COUNT = 6


def path_of(i: int) -> str:
    return f"shots/file_{i:02d}.png"


def current_hash(page) -> str:
    return page.evaluate("location.hash")


def test_a_cold_load_opens_the_file_the_hash_names(spa_backend, spa_page):
    """A shared link opens its own file, and only that one.

    Kills `if (!hashFile)` inverted — the hash is honoured and then overridden,
    so file 0 ends up selected after a second load start — and
    `var hashFile = false`, where the hash is never read and file 0 is
    selected, writing its own name over the hash that was shared.
    """
    backend = spa_backend(file_count=FILE_COUNT)
    page = spa_page(LOAD_COUNTER)
    page.goto(backend.pr_url("#file_03.png"), wait_until="load")

    wait_until(lambda: active_path(page) is not None, "a file to be selected")
    # Settled: the selection is made synchronously inside the /images callback,
    # so by the time any row is active both call sites have already run.
    assert active_path(page) == path_of(3)
    assert load_starts(page) == 1, "the first file must not be loaded on the way"
    assert current_hash(page) == "#file_03.png"


def test_a_hash_that_names_no_file_falls_back_to_the_first(spa_backend, spa_page):
    """The other half of the same branch, so it is pinned from both sides.

    An unknown hash is a link to a file the PR no longer carries; the page
    opens the first file rather than nothing, and says which one in the hash.
    """
    backend = spa_backend(file_count=FILE_COUNT)
    page = spa_page()
    page.goto(backend.pr_url("#not_in_this_pr.png"), wait_until="load")

    wait_until(lambda: active_path(page) == path_of(0), "the first file to be selected")
    wait_until(lambda: current_hash(page) == "#file_00.png", "the hash to name it")


def test_editing_the_hash_after_load_moves_the_selection(spa_backend, spa_page):
    """A hash set in place selects its file, whichever file it names.

    Kills deleting the `hashchange` listener, and the listener selecting
    `state.images[0].path`: under both the selection stays on file 0. Two
    successive edits, so a listener that honoured only the first could not
    pass either.
    """
    backend = spa_backend(file_count=FILE_COUNT)
    page = spa_page()
    page.goto(backend.pr_url(), wait_until="load")
    wait_until(lambda: active_path(page) == path_of(0), "the first file to be selected")

    page.evaluate("location.hash = '#file_04.png'")
    wait_until(lambda: active_path(page) == path_of(4), "the edited hash to select file 4")
    page.evaluate("location.hash = '#file_02.png'")
    wait_until(lambda: active_path(page) == path_of(2), "a second edit to select file 2")


def test_the_back_button_returns_to_the_previous_file(spa_backend, spa_page):
    """History is the hash, so going back is a hashchange like any other.

    The user-facing form of the same listener: each click writes the hash, and
    Back has to undo the selection as well as the URL.
    """
    backend = spa_backend(file_count=FILE_COUNT)
    page = spa_page()
    page.goto(backend.pr_url(), wait_until="load")
    wait_until(lambda: active_path(page) == path_of(0), "the first file to be selected")

    page.click(f'.file-item[data-path="{path_of(2)}"]')
    wait_until(lambda: current_hash(page) == "#file_02.png", "the click to write the hash")
    page.click(f'.file-item[data-path="{path_of(5)}"]')
    wait_until(lambda: current_hash(page) == "#file_05.png", "the second click to write it")

    page.go_back()
    wait_until(lambda: current_hash(page) == "#file_02.png", "Back to restore the hash")
    wait_until(lambda: active_path(page) == path_of(2), "Back to restore the selection")
