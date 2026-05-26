"""Compose + fallback tests for the bulletin issue builder.

LLM path is mocked — we never want a test that depends on a real
Anthropic call. The fallback path is verified to handle empty/sparse
states gracefully so a fresh-stack daemon still ships an issue.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from bulletin.issue import (
    LLM_MODEL,
    ComposedIssue,
    _fallback_compose,
    compose,
)
from bulletin.state import BulletinSnapshot

pytestmark = pytest.mark.asyncio


def _snap(**overrides) -> BulletinSnapshot:
    """Sane defaults for a typical week."""
    base = dict(
        window_days=7,
        issue_date="2026-05-26",
        starting_capital_usd="10000.00",
        equity_usd="9500.00",
        equity_delta_pct="-5.0000",
        n_trades_window=120,
        n_wins_window=15,
        win_rate_window="0.1250",
        total_pnl_usd_window="-500.00",
        by_strategy=[
            {"strategy_id": "matrix_agent", "n_trades": 80,
             "total_pnl_usd": "-300.00", "win_rate": "0.10"},
            {"strategy_id": "grid", "n_trades": 40,
             "total_pnl_usd": "-200.00", "win_rate": "0.18"},
        ],
        active_strategies=[
            {"strategy_id": "matrix_agent", "asset_class": "crypto",
             "version": 2, "cert": "no_cert"}
        ],
        new_mutations=[
            {"strategy_id": "matrix_agent", "from_v": 1, "to_v": 2,
             "type": "weight_tune", "source": "rule", "status": "applied",
             "at": "2026-05-25T18:47:46+00:00"}
        ],
        cert_changes=[],
        asset_signals=[
            {"asset": "BTC", "mentions": 12, "direct_polarity": "0.6",
             "contextual_polarity": "0.4"},
        ],
    )
    base.update(overrides)
    return BulletinSnapshot(**base)


def test_fallback_includes_required_sections():
    out = _fallback_compose(_snap())
    md = out.body_md
    for needle in (
        "# Matrix bulletin",
        "## What happened this week",
        "## What the engine learned",
        "## What the news graph saw",
        "## Next",
    ):
        assert needle in md, f"missing section: {needle}"
    assert out.model is None  # template path doesn't set model
    assert out.slug.startswith("weekly-2026-05-26-")


def test_fallback_carries_concrete_numbers_through():
    out = _fallback_compose(_snap())
    md = out.body_md
    assert "120" in md       # n_trades
    assert "9500" in md      # equity
    assert "matrix_agent" in md
    assert "BTC" in md


def test_fallback_handles_empty_state():
    """Fresh-stack day: no trades, no mutations, no graph signals.
    Must still produce a valid issue, not crash."""
    snap = _snap(
        n_trades_window=0, n_wins_window=0, win_rate_window="0",
        total_pnl_usd_window="0.00", by_strategy=[],
        active_strategies=[], new_mutations=[], cert_changes=[],
        asset_signals=[],
    )
    out = _fallback_compose(snap)
    assert "No strategies booked outcomes" in out.body_md
    assert "didn't propose any mutations" in out.body_md
    assert "Graph signals went quiet" in out.body_md


def test_fallback_surfaces_cert_grants_when_present():
    snap = _snap(cert_changes=[
        {"strategy_id": "matrix_agent", "version": 2, "asset_class": "crypto",
         "event": "granted", "at": "2026-05-26T01:00:00+00:00"}
    ])
    out = _fallback_compose(snap)
    assert "paper_trade_certificate **granted**" in out.body_md


async def test_compose_uses_llm_when_available(monkeypatch):
    """compose() should call call_claude_json and use its output."""
    fake_response = {
        "title": "Test LLM Title",
        "summary": "LLM-written summary.",
        "body_md": "# Test LLM Title\n\nThis is the LLM-written body.",
    }

    async def _fake_call_claude_json(*, system, user, model, max_tokens, temperature):
        # Should receive the LLM_MODEL
        assert model == LLM_MODEL
        assert "Compose this week's bulletin" in user
        return fake_response

    # Patch the import inside compose()
    import matrix_shared
    monkeypatch.setattr(matrix_shared, "llm_enabled", lambda: True)
    monkeypatch.setattr(matrix_shared, "call_claude_json", _fake_call_claude_json)

    out = await compose(_snap())
    assert out.title == "Test LLM Title"
    assert out.body_md.startswith("# Test LLM Title")
    assert out.model == LLM_MODEL


async def test_compose_falls_back_when_llm_disabled(monkeypatch):
    import matrix_shared
    monkeypatch.setattr(matrix_shared, "llm_enabled", lambda: False)
    out = await compose(_snap())
    assert out.model is None
    assert "# Matrix bulletin" in out.body_md


async def test_compose_falls_back_on_llm_exception(monkeypatch):
    async def _broken(**_kwargs):
        raise RuntimeError("simulated upstream timeout")

    import matrix_shared
    monkeypatch.setattr(matrix_shared, "llm_enabled", lambda: True)
    monkeypatch.setattr(matrix_shared, "call_claude_json", _broken)
    out = await compose(_snap())
    assert out.model is None
    assert "# Matrix bulletin" in out.body_md


async def test_compose_falls_back_on_empty_body(monkeypatch):
    """LLM returns garbage with missing body_md → fallback."""
    async def _empty_body(**_kwargs):
        return {"title": "x", "summary": "y", "body_md": ""}

    import matrix_shared
    monkeypatch.setattr(matrix_shared, "llm_enabled", lambda: True)
    monkeypatch.setattr(matrix_shared, "call_claude_json", _empty_body)
    out = await compose(_snap())
    assert out.model is None  # fallback
