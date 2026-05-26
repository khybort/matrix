"""tests/test_forbidden_path_gate.py

FORBIDDEN_PATHS was opened on 2026-05-26 — these tests now assert the
new policy: dev_agent's autonomous edits are permitted everywhere,
including services/strategy, services/agent, services/execution.

If you re-close the gate (repopulate the tuple in dev_agent.config),
flip these expectations back to assert pytest.raises(ForbiddenPathError).
The runtime live-execution wall (paper_trade_certificate) is enforced
separately in matrix_shared.trading_safety and is not exercised here.
"""

from __future__ import annotations

import pytest

from dev_agent.config import FORBIDDEN_PATHS
from dev_agent.safety import check_tool_call


def test_forbidden_paths_is_empty():
    """Policy invariant — gate is open. Inverting this needs an operator decision."""
    assert FORBIDDEN_PATHS == ()


@pytest.mark.parametrize("path", [
    "services/strategy/momentum.py",
    "services/strategy/utils/foo.py",
    "services/agent/main.py",
    "services/execution/bybit.py",
])
def test_edit_to_trading_path_now_allowed(path):
    # Must not raise — gate is open.
    check_tool_call(tool_name="Edit", params={"file_path": path})


@pytest.mark.parametrize("path", [
    "services/strategy/momentum.py",
    "services/agent/main.py",
])
def test_write_to_trading_path_now_allowed(path):
    check_tool_call(tool_name="Write", params={"file_path": path})


@pytest.mark.parametrize("tool_name", ["Read", "Grep", "Glob", "Bash"])
def test_read_only_tools_allowed_on_trading_paths(tool_name):
    params = (
        {"file_path": "services/strategy/momentum.py"}
        if tool_name == "Read"
        else {"pattern": "x", "path": "services/strategy/"}
    )
    check_tool_call(tool_name=tool_name, params=params)  # must not raise


@pytest.mark.parametrize("path", [
    "services/graph/extract.py",
    "services/dev_agent/main.py",
    "packages/python-shared/src/foo.py",
    "docs/ARCHITECTURE.md",
])
def test_edit_to_other_paths_still_allowed(path):
    check_tool_call(tool_name="Edit", params={"file_path": path})
