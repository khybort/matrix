"""Worker pattern — Haiku-side context distillation for tool outputs.

Host-only tests: when subscription is not configured the helper returns a
deterministic truncated fallback so the calling tool always has *something*
to return to the orchestrator. The live Haiku path is exercised against the
running stack.
"""

from __future__ import annotations

import pytest

from matrix_shared.agent_runtime.worker import (
    _deterministic_truncate,
    haiku_distill,
)


# ---- deterministic fallback (pure) ----

def test_truncate_short_string_passes_through():
    assert _deterministic_truncate("hello", max_chars=100) == "hello"


def test_truncate_long_string_caps_with_ellipsis():
    out = _deterministic_truncate("x" * 1000, max_chars=200)
    assert len(out) == 200
    assert out.endswith("…")


def test_truncate_dict_serializes_then_caps():
    out = _deterministic_truncate({"a": 1, "b": 2}, max_chars=100)
    assert out.startswith("{")
    assert '"a":1' in out


def test_truncate_list_serializes_then_caps():
    out = _deterministic_truncate([{"i": i} for i in range(50)], max_chars=120)
    assert len(out) == 120
    assert out.endswith("…")


# ---- haiku_distill ----

@pytest.mark.asyncio
async def test_haiku_distill_falls_back_when_subscription_disabled(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    out = await haiku_distill(
        raw="some long text " * 200,
        instruction="bullets please",
        max_tokens=300,
    )
    # Fallback returns truncated string instead of calling Haiku.
    assert isinstance(out, str)
    assert len(out) <= 800  # default truncation budget
    assert out  # non-empty
