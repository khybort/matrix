"""tests/test_forbidden_path_gate.py

Policy since 2026-09-12: dev_agent may edit anywhere EXCEPT the three
live-capital gate files (trading_safety, exchange_shadow, execution.safety).
services/strategy, services/agent and the rest of services/execution stay open.
The runtime live-execution wall (paper_trade_certificate) is enforced
separately in matrix_shared.trading_safety and is not exercised here.
"""

from __future__ import annotations

import pytest

from dev_agent.config import FORBIDDEN_PATHS
from dev_agent.safety import check_tool_call


def test_forbidden_paths_protect_only_live_capital_gates():
    """Policy invariant (2026-09-12): exactly the three live-capital gate
    files are closed; everything else is open."""
    assert FORBIDDEN_PATHS == (
        "packages/python-shared/src/matrix_shared/trading_safety.py",
        "packages/python-shared/src/matrix_shared/exchange_shadow.py",
        "services/execution/src/execution/safety.py",
    )


@pytest.mark.parametrize("path", [
    "services/execution/src/execution/safety.py",
    "/workspace/worktrees/dev-agent/task-3/services/execution/src/execution/safety.py",
    "packages/python-shared/src/matrix_shared/trading_safety.py",
    "/workspace/worktrees/dev-agent/task-3/packages/python-shared/src/matrix_shared/exchange_shadow.py",
])
def test_edit_to_live_gate_file_is_refused(path):
    from dev_agent.safety import ForbiddenPathError
    with pytest.raises(ForbiddenPathError):
        check_tool_call(tool_name="Edit", params={"file_path": path})


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
