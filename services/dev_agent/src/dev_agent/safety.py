"""Safety gates for dev_agent. All gates raise structured exceptions that
the SDK runner catches to abort the task with a specific failure_reason."""

from __future__ import annotations

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
