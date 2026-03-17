"""Pytest runner for Bazel py_test."""

import sys

import pytest

sys.exit(pytest.main(["-v"] + sys.argv[1:]))
