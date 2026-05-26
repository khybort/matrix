"""Pure-function tests for the alert state machine + formatters.

No DB. No Telegram. Just feed snapshots in, assert alert fan-out.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from notify.alerts import (
    ALERT_INFO,
    ALERT_URGENT,
    ALERT_WARNING,
    PollSnapshot,
    detect_alerts,
    format_circuit,
    format_help,
    format_status,
    format_strategies,
)
from notify.state import WalletSnapshot


def _wallet(
    *,
    equity: str = "10000",
    cash: str = "10000",
    locked: str = "0",
    tripped: datetime | None = None,
    starting: str = "10000",
    name: str = "default",
) -> WalletSnapshot:
    cash_d = Decimal(cash)
    locked_d = Decimal(locked)
    starting_d = Decimal(starting)
    eq = Decimal(equity)
    net = eq - starting_d
    return WalletSnapshot(
        wallet_id="00000000-0000-0000-0000-000000000001",
        name=name,
        starting_capital_usd=starting_d,
        cash_usd=cash_d,
        locked_usd=locked_d,
        equity_usd=eq,
        net_pnl_usd=net,
        net_pnl_pct=(net / starting_d * Decimal(100)),
        circuit_tripped_at=tripped,
        max_position_pct=Decimal("0.02"),
        max_concurrent_positions=5,
        daily_loss_circuit_pct=Decimal("0.05"),
    )


# ---- urgent: circuit tripped ----


def test_circuit_trip_fires_once_per_event(monkeypatch):
    monkeypatch.delenv("LIVE_EXECUTION_ENABLED", raising=False)
    w0 = _wallet()
    w1 = _wallet(tripped=datetime(2026, 5, 26, 12, tzinfo=timezone.utc))
    prev = PollSnapshot(wallet=w0, last_daily_summary_date="2026-05-26")
    alerts, new = detect_alerts(prev, w1, {})
    levels = [lvl for lvl, _ in alerts]
    assert ALERT_URGENT in levels
    assert any("circuit TRIPPED" in t.lower() or "circuit tripped" in t.lower() for _, t in alerts)
    # Second pass with same wallet: NO re-alert
    alerts2, _ = detect_alerts(new, w1, {})
    assert ALERT_URGENT not in [lvl for lvl, _ in alerts2]


# ---- urgent: LIVE_EXECUTION_ENABLED flip ----


def test_live_flag_flip_urgent(monkeypatch):
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "true")
    prev = PollSnapshot(live_execution_enabled=False, last_daily_summary_date="2026-05-26")
    alerts, _ = detect_alerts(prev, None, {})
    assert any(lvl == ALERT_URGENT and "LIVE_EXECUTION_ENABLED" in t for lvl, t in alerts)


def test_live_flag_drop_info(monkeypatch):
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "false")
    prev = PollSnapshot(live_execution_enabled=True, last_daily_summary_date="2026-05-26")
    alerts, _ = detect_alerts(prev, None, {})
    assert any(lvl == ALERT_INFO and "FALSE" in t for lvl, t in alerts)


# ---- warning: equity drop ----


def test_equity_drop_warning(monkeypatch):
    monkeypatch.delenv("LIVE_EXECUTION_ENABLED", raising=False)
    w0 = _wallet(equity="10000")
    w1 = _wallet(equity="9800")  # -2% drop
    prev = PollSnapshot(wallet=w0, last_daily_summary_date="2026-05-26")
    alerts, _ = detect_alerts(prev, w1, {})
    assert any(lvl == ALERT_WARNING and "Equity dropped" in t for lvl, t in alerts)


def test_equity_small_drop_no_warning(monkeypatch):
    """0.5% drop is below the 1% default threshold → no alert."""
    monkeypatch.delenv("LIVE_EXECUTION_ENABLED", raising=False)
    w0 = _wallet(equity="10000")
    w1 = _wallet(equity="9950")
    prev = PollSnapshot(wallet=w0, last_daily_summary_date="2026-05-26")
    alerts, _ = detect_alerts(prev, w1, {})
    assert not any("Equity dropped" in t for _, t in alerts)


# ---- info: cert state changes ----


def test_cert_granted_info(monkeypatch):
    monkeypatch.delenv("LIVE_EXECUTION_ENABLED", raising=False)
    prev = PollSnapshot(
        active_certs={("matrix_agent", "crypto", 1): "no_cert"},
        last_daily_summary_date="2026-05-26",
    )
    curr_certs = {("matrix_agent", "crypto", 1): "valid"}
    alerts, _ = detect_alerts(prev, None, curr_certs)
    assert any(lvl == ALERT_INFO and "GRANTED" in t for lvl, t in alerts)


def test_cert_expired_warning(monkeypatch):
    monkeypatch.delenv("LIVE_EXECUTION_ENABLED", raising=False)
    prev = PollSnapshot(
        active_certs={("matrix_agent", "crypto", 1): "valid"},
        last_daily_summary_date="2026-05-26",
    )
    curr_certs = {("matrix_agent", "crypto", 1): "expired"}
    alerts, _ = detect_alerts(prev, None, curr_certs)
    assert any(lvl == ALERT_WARNING and "expired" in t for lvl, t in alerts)


# ---- daily summary ----


def test_daily_summary_fires_on_date_change(monkeypatch):
    monkeypatch.delenv("LIVE_EXECUTION_ENABLED", raising=False)
    prev = PollSnapshot(last_daily_summary_date="2026-05-25")
    # Force now() inside detect_alerts via the `now` kwarg
    now = datetime(2026, 5, 26, 0, 5, tzinfo=timezone.utc)
    alerts, _ = detect_alerts(prev, None, {}, now=now)
    assert any("__DAILY_SUMMARY_DUE__" in t for _, t in alerts)


def test_daily_summary_same_day_silent(monkeypatch):
    monkeypatch.delenv("LIVE_EXECUTION_ENABLED", raising=False)
    prev = PollSnapshot(last_daily_summary_date="2026-05-26")
    now = datetime(2026, 5, 26, 23, 30, tzinfo=timezone.utc)
    alerts, _ = detect_alerts(prev, None, {}, now=now)
    assert not any("__DAILY_SUMMARY_DUE__" in t for _, t in alerts)


# ---- formatters ----


def test_format_status_handles_missing_wallet():
    out = format_status(None, {"total": 0, "by_strategy": {}}, {"window_hours": 24, "n_outcomes": 0, "total_pnl_usd": "0", "win_rate": None})
    assert "No wallet" in out


def test_format_status_renders_wallet_lines():
    w = _wallet(equity="10500", cash="9000", locked="1500")
    pos = {"total": 2, "by_strategy": {"grid": {"n": 2, "notional_usd": "200.00"}}}
    pnl = {"window_hours": 24, "n_outcomes": 5, "total_pnl_usd": "12.34", "win_rate": 0.6}
    out = format_status(w, pos, pnl)
    assert "10500" in out
    assert "grid" in out
    assert "60.0%" in out


def test_format_circuit_clean_vs_tripped():
    w_clean = _wallet()
    w_trip = _wallet(tripped=datetime(2026, 5, 26, 12, tzinfo=timezone.utc))
    assert "Circuit clean" in format_circuit(w_clean)
    assert "Circuit tripped" in format_circuit(w_trip)


def test_format_strategies_with_rows():
    rows = [
        {"strategy_id": "matrix_agent", "asset_class": "crypto", "version": 1, "cert_state": "valid"},
        {"strategy_id": "grid", "asset_class": "crypto", "version": 1, "cert_state": "no_cert"},
    ]
    out = format_strategies(rows)
    assert "matrix_agent" in out and "grid" in out
    assert "✅" in out  # valid → green check


def test_format_help_lists_commands():
    out = format_help()
    for cmd in ("/status", "/strategies", "/circuit", "/help"):
        assert cmd in out
