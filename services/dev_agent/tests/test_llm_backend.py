"""Tests for dev_agent LLM backend selection."""

from __future__ import annotations

from dev_agent.llm_backend import (
    claude_sdk_mode,
    cursor_task_mode,
    llm_ready,
    resolve_claude_model,
)


def test_cursor_mode_when_backend_cursor_and_api_key(monkeypatch):
    monkeypatch.setenv("MATRIX_LLM_BACKEND", "cursor")
    monkeypatch.setenv("CURSOR_API_KEY", "test-key")
    assert cursor_task_mode() is True
    assert llm_ready() is True


def test_claude_sdk_mode_with_oauth(monkeypatch):
    monkeypatch.delenv("MATRIX_LLM_BACKEND", raising=False)
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-token")
    assert claude_sdk_mode() is True
    assert llm_ready() is True


def test_llm_not_ready_without_auth(monkeypatch):
    monkeypatch.delenv("MATRIX_LLM_BACKEND", raising=False)
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr(
        "matrix_shared.cursor_llm._cli_logged_in_sync",
        lambda: False,
    )
    assert llm_ready() is False


def test_resolve_claude_model_prefers_env(monkeypatch):
    monkeypatch.setenv("MATRIX_MODEL_SONNET", "claude-sonnet-4-6")
    assert resolve_claude_model("us.anthropic.claude-sonnet-4-6") == "claude-sonnet-4-6"
