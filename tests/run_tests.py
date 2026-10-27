"""Pytest runner for Bazel py_test.

Bazel hands ``--test_filter`` to a test as the ``TESTBRIDGE_TEST_ONLY``
environment variable, not as an argument, so it is passed on here as pytest's
``-k`` (#48). Without that the filter was silently ignored and every run was
the whole suite.

The filter is therefore a ``-k`` expression, not a node id:
``--test_filter=TestRepoPulls`` or ``--test_filter='TestRepoPulls and empty'``
work, ``Class::test`` selects nothing. A filter that deselects everything makes
pytest exit 5, which Bazel reports as a failing target rather than as a bad
filter.
"""

import os
import sys

import pytest


def pytest_args(argv, environ):
    args = ["-v"] + list(argv)
    test_filter = environ.get("TESTBRIDGE_TEST_ONLY")
    if test_filter:
        args += ["-k", test_filter]
    return args


if __name__ == "__main__":
    sys.exit(pytest.main(pytest_args(sys.argv[1:], os.environ)))
