"""tests/test_forbidden_path_gate.py"""

from __future__ import annotations

import pytest

from dev_agent.safety import ForbiddenPathError, check_tool_call


@pytest.mark.parametrize("path", [
    "services/strategy/momentum.py",
    "services/strategy/utils/foo.py",
    "services/agent/main.py",
    "services/execution/bybit.py",
])
def test_edit_to_forbidden_path_raises(path):
    with pytest.raises(ForbiddenPathError) as excinfo:
        check_tool_call(tool_name="Edit", params={"file_path": path})
    assert path in str(excinfo.value)


@pytest.mark.parametrize("path", [
    "services/strategy/momentum.py",
    "services/agent/main.py",
])
def test_write_to_forbidden_path_raises(path):
    with pytest.raises(ForbiddenPathError):
        check_tool_call(tool_name="Write", params={"file_path": path})


@pytest.mark.parametrize("tool_name", ["Read", "Grep", "Glob", "Bash"])
def test_read_only_tools_allowed_on_forbidden_paths(tool_name):
    params = {"file_path": "services/strategy/momentum.py"} if tool_name == "Read" else {"pattern": "x", "path": "services/strategy/"}
    check_tool_call(tool_name=tool_name, params=params)  # must not raise


@pytest.mark.parametrize("path", [
    "services/graph/extract.py",
    "services/dev_agent/main.py",
    "packages/python-shared/src/foo.py",
    "docs/ARCHITECTURE.md",
])
def test_edit_to_allowed_path_does_not_raise(path):
    check_tool_call(tool_name="Edit", params={"file_path": path})


def test_path_with_workspace_prefix_still_caught():
    """Agent runs in /workspace/worktrees/.../task-N/, so file_path may include
    a worktree prefix. The gate must still catch trading paths inside that."""
    with pytest.raises(ForbiddenPathError):
        check_tool_call(
            tool_name="Edit",
            params={"file_path": "/workspace/worktrees/dev-agent/task-42/services/strategy/x.py"},
        )


def test_relative_dotdot_traversal_is_caught():
    """Defense against ../../../services/strategy/x.py style escapes."""
    with pytest.raises(ForbiddenPathError):
        check_tool_call(
            tool_name="Edit",
            params={"file_path": "../../services/strategy/x.py"},
        )
