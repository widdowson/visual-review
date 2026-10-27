"""spa_browser_test: a py_test that drives static/index.html in a real browser.

Everything a browser test needs that is not its own test file lives here, so a
new one is its name and its srcs:

    spa_browser_test(
        name = "test_spa_sidebar",
        srcs = ["tests/test_spa_sidebar.py"],
    )

What that adds, and why each piece is not optional:

- `tests/conftest.py`, `tests/fixture_server.py` and `tests/spa_harness.py` —
  the shared fixtures, the fake backend and the instruments.
- `static/index.html` in data. It is the code under test, and without it an
  index.html-only edit changes no input of the target, which then takes a
  cache hit instead of running against the change.
- The hermetic Chromium and the variable that locates it.
- `VR_AUTH_MODE=disabled`, because the fixture server imports app.py and the
  module raises at import under any configuration that does not set it.
- `:app_lib`, for the same import: the fixture asks app.py for the image
  Cache-Control rather than copying it.

`size` defaults to "medium": a mutant that breaks loading spends its time in
wait_until timeouts before it reports, and a browser is not a small workload.
"""

load("@pip//:requirements.bzl", "requirement")
load("@rules_python//python:py_test.bzl", "py_test")

_HARNESS_SRCS = [
    "tests/__init__.py",
    "tests/conftest.py",
    "tests/fixture_server.py",
    "tests/run_tests.py",
    "tests/spa_harness.py",
]

def spa_browser_test(name, srcs, data = [], deps = [], env = {}, size = "medium", tags = [], **kwargs):
    """A browser-driven SPA test. `srcs` are the test modules; the first is run.

    Args:
      name: target name.
      srcs: the test module, and any helpers of its own.
      data: extra runfiles beyond the page and the browser.
      deps: extra Python deps beyond playwright, pytest and :app_lib.
      env: extra environment, merged over the harness's own.
      size: Bazel test size.
      tags: extra tags; "browser" is always added.
      **kwargs: passed through to py_test.
    """
    py_test(
        name = name,
        size = size,
        srcs = _HARNESS_SRCS + [s for s in srcs if s not in _HARNESS_SRCS],
        args = [srcs[0]],
        data = [
            "//:static/index.html",
            "@playwright//:chromium-headless-shell",
        ] + data,
        env = dict({
            "BAZEL_PLAYWRIGHT_CHROMIUM": "$(rlocationpath @playwright//:chromium-headless-shell)",
            "VR_AUTH_MODE": "disabled",
        }, **env),
        main = "tests/run_tests.py",
        tags = ["browser"] + [t for t in tags if t != "browser"],
        deps = [
            "//:app_lib",
            requirement("playwright"),
            requirement("pytest"),
        ] + deps,
        **kwargs
    )
