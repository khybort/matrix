"""tests/test_tool_loop_detector.py"""

from __future__ import annotations

import pytest

from dev_agent.safety import ToolLoopDetector, ToolLoopError


def test_single_tool_call_no_raise():
    d = ToolLoopDetector(threshold=5)
    d.record(tool_name="Read", params={"file_path": "a.py"})


def test_four_identical_calls_no_raise():
    d = ToolLoopDetector(threshold=5)
    for _ in range(4):
        d.record(tool_name="Read", params={"file_path": "a.py"})


def test_five_identical_calls_raise():
    d = ToolLoopDetector(threshold=5)
    with pytest.raises(ToolLoopError):
        for _ in range(5):
            d.record(tool_name="Read", params={"file_path": "a.py"})


def test_intervening_different_call_resets_streak():
    d = ToolLoopDetector(threshold=3)
    d.record(tool_name="Read", params={"file_path": "a.py"})
    d.record(tool_name="Read", params={"file_path": "a.py"})
    d.record(tool_name="Read", params={"file_path": "b.py"})
    d.record(tool_name="Read", params={"file_path": "a.py"})
    d.record(tool_name="Read", params={"file_path": "a.py"})


def test_different_tool_same_params_not_a_loop():
    d = ToolLoopDetector(threshold=3)
    d.record(tool_name="Read", params={"file_path": "a.py"})
    d.record(tool_name="Edit", params={"file_path": "a.py"})
    d.record(tool_name="Read", params={"file_path": "a.py"})
