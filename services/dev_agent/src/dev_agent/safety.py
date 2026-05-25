"""Safety gates for dev_agent. All gates raise structured exceptions that
the SDK runner catches to abort the task with a specific failure_reason."""

from __future__ import annotations

import json
import os
from pathlib import PurePosixPath

from dev_agent.config import FORBIDDEN_PATHS


class SafetyError(Exception):
    """Base class for safety-gate violations."""

    reason: str = "safety_violation"


class ForbiddenPathError(SafetyError):
    reason = "trading_path_violation"

    def __init__(self, path: str) -> None:
        super().__init__(f"forbidden path: {path}")
        self.path = path


WRITE_TOOLS = frozenset({"Edit", "Write", "NotebookEdit"})


def check_tool_call(tool_name: str, params: dict) -> None:
    """Raise SafetyError if this tool call is forbidden.

    Called from the SDK `can_use_tool` callback BEFORE the tool runs.
    """
    if tool_name in WRITE_TOOLS:
        path = _normalize(params.get("file_path", ""))
        for forbidden in FORBIDDEN_PATHS:
            if _matches_forbidden(path, forbidden):
                raise ForbiddenPathError(path)


def _normalize(raw: str) -> str:
    if not raw:
        return ""
    p = PurePosixPath(raw)
    parts = p.parts
    for i, part in enumerate(parts):
        if part == "services" and i + 1 < len(parts):
            tail = "/".join(parts[i:])
            if not tail.endswith("/"):
                tail += "/"
            return tail
    norm = os.path.normpath(raw)
    if norm.startswith("../") or "/../" in norm:
        return norm + ("/" if not norm.endswith("/") else "")
    return raw + ("/" if not raw.endswith("/") else "")


def _matches_forbidden(path: str, forbidden: str) -> bool:
    return path.startswith(forbidden) or ("/" + forbidden) in path


class CostCapError(SafetyError):
    reason = "cost_cap_task"

    def __init__(self, total: float, cap: float) -> None:
        super().__init__(f"task cost cap exceeded: {total:.4f} > {cap:.4f}")
        self.total = total
        self.cap = cap


class CostCap:
    """Per-task cost accumulator. Raises when total exceeds the cap."""

    def __init__(self, per_task_cap_usd: float) -> None:
        self.cap = per_task_cap_usd
        self.total = 0.0

    def add(self, delta_usd: float) -> None:
        if delta_usd <= 0:
            return
        self.total += delta_usd
        if self.total > self.cap:
            raise CostCapError(self.total, self.cap)


class ToolLoopError(SafetyError):
    reason = "tool_loop"

    def __init__(self, tool_name: str, params: dict, count: int) -> None:
        super().__init__(f"tool loop detected: {tool_name} called {count} times with identical params")
        self.tool_name = tool_name
        self.params = params
        self.count = count


class ToolLoopDetector:
    """Detects N consecutive identical tool calls and raises ToolLoopError."""

    def __init__(self, threshold: int = 5) -> None:
        self.threshold = threshold
        self._last_key: str | None = None
        self._streak = 0

    def record(self, tool_name: str, params: dict) -> None:
        key = self._fingerprint(tool_name, params)
        if key == self._last_key:
            self._streak += 1
        else:
            self._last_key = key
            self._streak = 1
        if self._streak >= self.threshold:
            raise ToolLoopError(tool_name, params, self._streak)

    @staticmethod
    def _fingerprint(tool_name: str, params: dict) -> str:
        return f"{tool_name}:{json.dumps(params, sort_keys=True, default=str)}"
