"""LLM backend selection for dev_agent (Cursor Auto vs Claude Code SDK)."""

from __future__ import annotations

import os


def cursor_task_mode() -> bool:
    from matrix_shared.cursor_llm import cursor_enabled
    from matrix_shared.subscription_llm import _cursor_primary

    return _cursor_primary() and cursor_enabled()


def claude_sdk_mode() -> bool:
    return bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip())


def llm_ready() -> bool:
    return cursor_task_mode() or claude_sdk_mode()


def resolve_claude_model(task_model: str | None) -> str:
    """Subscription-safe model id for claude_agent_sdk (not Bedrock ARNs)."""
    if os.environ.get("CLAUDE_CODE_USE_BEDROCK"):
        return (
            os.environ.get("MATRIX_MODEL_SONNET")
            or task_model
            or "claude-sonnet-4-6"
        )
    return (
        os.environ.get("MATRIX_MODEL_SONNET")
        or task_model
        or "claude-sonnet-4-6"
    )
