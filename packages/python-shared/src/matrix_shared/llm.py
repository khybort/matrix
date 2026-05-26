"""Shared LLM helper — thin shim over `subscription_llm`.

All LLM calls in the project route through the Claude Code subscription
(claude_agent_sdk) since 2026-05-26. `ANTHROPIC_API_KEY` is no longer
used. This file keeps the historical `call_claude` / `call_claude_json`
names so call sites don't need updating — they just delegate.

If you ever want to route through the Anthropic Messages API again
(e.g. for production billing separation), swap the body of these two
functions back to the httpx implementation in git history.
"""

from __future__ import annotations

from matrix_shared.subscription_llm import (
    call_subscription,
    call_subscription_json,
    subscription_enabled,
)


def llm_enabled() -> bool:
    """True when an LLM path is available (CLAUDE_CODE_OAUTH_TOKEN set)."""
    return subscription_enabled()


async def call_claude(
    *,
    user: str,
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 800,
    temperature: float = 0.2,
    timeout_s: float = 35.0,  # accepted for backward compat; SDK has its own
) -> str | None:
    return await call_subscription(
        user=user,
        system=system,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
    )


async def call_claude_json(
    *,
    user: str,
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 1200,
    temperature: float = 0.2,
) -> dict | None:
    return await call_subscription_json(
        user=user,
        system=system,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
    )
