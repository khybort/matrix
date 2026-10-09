"""The 24h win rate counts bets, not re-filled rows."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from notify.state import recent_pnl_summary

T0 = datetime(2026, 10, 1, tzinfo=UTC)


def _row(i_s: int, pnl: float) -> dict:
    return {"strategy_id": "s", "asset_class": "crypto", "symbol": "BTCUSDT", "side": "long",
            "generated_at": T0 + timedelta(seconds=i_s), "horizon_seconds": 600, "pnl_usd": pnl}


def test_recent_pnl_counts_episodes():
    rows = [_row(10 * i, 1.0) for i in range(9)] + [_row(3600, -2.0)]
    out = recent_pnl_summary(rows, 24)
    assert (out["n_outcomes"], out["n_raw"], out["wins"], out["losses"]) == (2, 10, 1, 1)
    assert out["win_rate"] == 0.5 and float(out["total_pnl_usd"]) == 7.0


def test_recent_pnl_empty():
    assert recent_pnl_summary([], 24)["win_rate"] is None
