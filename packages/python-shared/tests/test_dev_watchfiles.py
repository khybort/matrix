"""Tests for dev_watchfiles filter (load module directly — avoids Settings import)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from watchfiles import Change

_path = Path(__file__).resolve().parents[1] / "src" / "matrix_shared" / "dev_watchfiles.py"
_spec = importlib.util.spec_from_file_location("matrix_shared_dev_watchfiles", _path)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
MatrixDevFilter = _mod.MatrixDevFilter


def test_matrix_dev_filter_skips_tests_tree() -> None:
    f = MatrixDevFilter()
    assert f(Change.modified, "/workspace/services/labs/src/labs/main.py")
    assert not f(Change.modified, "/workspace/services/labs/tests/test_foo.py")


def test_matrix_dev_filter_skips_pycache() -> None:
    f = MatrixDevFilter()
    assert not f(Change.added, "/workspace/services/labs/src/labs/__pycache__/x.cpython-313.pyc")
