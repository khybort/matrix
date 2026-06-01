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
async def test_yields_nothing_when_no_llm_backend(monkeypatch):
    """Container .env often has Bedrock/OAuth; blank every backend for this test."""
    for key in (
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CURSOR_API_KEY",
        "MATRIX_LLM_BACKEND",
        "AWS_ACCESS_KEY_ID",
    ):
        monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv(key, "")
    events = [e async for e in call_subscription_agent(prompt="hi")]
    assert events == []
