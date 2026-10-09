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
async def test_yields_nothing_when_no_llm_backend(monkeypatch, tmp_path):
    """Container .env often has Bedrock/OAuth; blank every backend for this test.
    The synced OAuth session file (/root/.claude) counts as a backend too, so
    point CLAUDE_CONFIG_DIR at an empty dir — otherwise this makes a real call."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr("matrix_shared.cursor_llm._cli_logged_in_sync", lambda: False)
    for key in (
        "OPENROUTER_API_KEY",
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
