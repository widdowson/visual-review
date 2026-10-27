"""The Bazel pytest runner passes ``--test_filter`` on as ``-k`` (#48).

Bazel delivers the filter as ``TESTBRIDGE_TEST_ONLY``. The runner used to read
only ``sys.argv``, so every filtered run was the whole suite and said nothing.

The end-to-end cases run the real ``run_tests.py`` as ``__main__`` against a
throwaway test file, because the unit cases alone would pass with the
``__main__`` block still calling ``pytest.main`` on ``sys.argv`` directly.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from run_tests import pytest_args  # noqa: E402

# Not .resolve(): under Bazel that follows the runfiles symlink out into the
# source tree, and the test would then exercise a file it never declared.
RUNNER = Path(os.path.abspath(__file__)).parent / "run_tests.py"

SAMPLE = '''
class TestAlpha:
    def test_one(self):
        pass

class TestBeta:
    def test_two(self):
        pass
'''


def test_no_filter_passes_argv_through():
    assert pytest_args(["tests/test_app.py"], {}) == ["-v", "tests/test_app.py"]


def test_filter_becomes_k():
    args = pytest_args(["tests/test_app.py"], {"TESTBRIDGE_TEST_ONLY": "TestRepoPulls"})
    assert args == ["-v", "tests/test_app.py", "-k", "TestRepoPulls"]


def test_empty_filter_is_no_filter():
    # Bazel can export the variable empty; `-k ''` would be harmless to pytest
    # but is not what anyone asked for.
    assert pytest_args([], {"TESTBRIDGE_TEST_ONLY": ""}) == ["-v"]


def _run(tmp_path, test_filter):
    sample = tmp_path / "test_sample.py"
    sample.write_text(SAMPLE)
    env = {k: v for k, v in os.environ.items() if k != "TESTBRIDGE_TEST_ONLY"}
    # The child needs the interpreter path Bazel assembled for this test, or it
    # cannot import pytest.
    env["PYTHONPATH"] = os.pathsep.join(sys.path)
    if test_filter is not None:
        env["TESTBRIDGE_TEST_ONLY"] = test_filter
    return subprocess.run(
        [sys.executable, str(RUNNER), "-p", "no:cacheprovider", str(sample)],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=60,
    )


def test_runner_runs_everything_without_a_filter(tmp_path):
    result = _run(tmp_path, None)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout


def test_runner_applies_the_filter(tmp_path):
    result = _run(tmp_path, "TestAlpha")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed, 1 deselected" in result.stdout
    assert "test_one" in result.stdout
    assert "test_two PASSED" not in result.stdout


def test_a_filter_matching_nothing_exits_5(tmp_path):
    # The trap the runner's docstring names: a node id is not a -k expression,
    # and pytest's "no tests ran" is a failing target to Bazel.
    result = _run(tmp_path, "TestAlpha::test_one")
    assert result.returncode == pytest.ExitCode.NO_TESTS_COLLECTED, result.stdout
