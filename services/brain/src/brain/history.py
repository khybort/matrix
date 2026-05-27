"""Conversation-history formatting (pure; DB I/O lives in sessions.py)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def format_history(messages: Sequence[dict[str, Any]], current: str) -> str:
    """Render prior turns + the new user message into a single prompt string.

    The subscription SDK takes a prompt string, so multi-turn context is
    replayed inline. `messages` is oldest-first [{role, content}, ...].
    """
    lines: list[str] = []
    for m in messages:
        role = str(m.get("role", "user")).upper()
        lines.append(f"{role}: {m.get('content', '')}")
    lines.append(f"USER: {current}")
    return "\n".join(lines)
