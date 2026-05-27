"""call_subscription_agent — the tool-loop entry point on the subscription path.

Keeps the project rule that every LLM call routes through subscription_llm.
Host-safe behavior: with no OAuth token configured it yields nothing (the
caller falls back to a deterministic path), exactly like call_subscription
returns None.
"""

from __future__ import annotations

import pytest

from matrix_shared.subscription_llm import call_subscription_agent


@pytest.mark.asyncio
async def test_yields_nothing_when_subscription_disabled(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    events = [e async for e in call_subscription_agent(prompt="hi")]
    assert events == []
