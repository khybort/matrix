"""Tests for Cursor LLM backend routing."""

from __future__ import annotations

import pytest

from matrix_shared.cursor_llm import (
    CURSOR_MODEL_AUTO,
    cursor_enabled,
    resolve_cursor_model,
)
from matrix_shared.subscription_llm import (
    _plan_backends,
    subscription_enabled,
)


@pytest.mark.asyncio
async def test_yields_nothing_when_no_backend(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_USE_BEDROCK", raising=False)
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.delenv("MATRIX_LLM_BACKEND", raising=False)
    monkeypatch.setattr(
        "matrix_shared.cursor_llm._cli_logged_in_sync",
        lambda: False,
    )

    from matrix_shared.subscription_llm import call_subscription_agent

    events = [e async for e in call_subscription_agent(prompt="hi")]
    assert events == []


def test_resolve_cursor_model_always_auto(monkeypatch):
    assert resolve_cursor_model(None) == CURSOR_MODEL_AUTO
    assert resolve_cursor_model("claude-sonnet-4-6") == CURSOR_MODEL_AUTO
    assert resolve_cursor_model("haiku") == CURSOR_MODEL_AUTO


def test_cursor_backend_enabled_with_api_key(monkeypatch):
    monkeypatch.setenv("MATRIX_LLM_BACKEND", "cursor")
    monkeypatch.setenv("CURSOR_API_KEY", "cursor_test_key")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)

    assert subscription_enabled() is True
    assert _plan_backends() == ["cursor"]
    assert cursor_enabled() is True


def test_cursor_backend_enabled_with_cli_login(monkeypatch):
    monkeypatch.setenv("MATRIX_LLM_BACKEND", "cursor")
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.setattr(
        "matrix_shared.cursor_llm._cli_logged_in_sync",
        lambda: True,
    )

    assert cursor_enabled() is True
    assert subscription_enabled() is True
    assert _plan_backends() == ["cursor"]


def test_cursor_backend_disabled_without_auth(monkeypatch):
    monkeypatch.setenv("MATRIX_LLM_BACKEND", "cursor")
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.setattr(
        "matrix_shared.cursor_llm._cli_logged_in_sync",
        lambda: False,
    )

    assert cursor_enabled() is False
    assert subscription_enabled() is False
    assert _plan_backends() == []


def test_status_output_logged_in():
    from matrix_shared.cursor_llm import _status_output_logged_in

    assert not _status_output_logged_in("Not logged in\n")
    assert not _status_output_logged_in("Authentication required")
    assert _status_output_logged_in("Logged in as user@example.com\n")
