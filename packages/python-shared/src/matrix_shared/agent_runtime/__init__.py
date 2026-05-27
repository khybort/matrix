"""Shared agent runtime for Matrix.

A thin layer built ON the Claude Code subscription SDK's native tool loop
(see `services/dev_agent/src/dev_agent/sdk_runner.py` for the proven
pattern), NOT LangGraph. Every LLM call still flows through the
subscription path (`CLAUDE_CODE_OAUTH_TOKEN`); no raw Anthropic API.

Modules:
    guards      — read-only SQL/Cypher gates for tool belts (defense in depth)
    ratelimit   — process-shared concurrency budget against the subscription
    tool        — Tool / ToolRegistry / @tool with side-effect classification
    runtime     — the SDK tool-loop driver (lazy SDK import; fake-able in tests)
"""

from __future__ import annotations

from matrix_shared.agent_runtime.guards import (
    UnsafeQueryError,
    ensure_read_only_cypher,
    ensure_read_only_sql,
)

__all__ = [
    "UnsafeQueryError",
    "ensure_read_only_cypher",
    "ensure_read_only_sql",
]
