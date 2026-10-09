"""Reflection's mutation gate and the LLM's outcome view count bets, not rows
(edge_study.episode_groups)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from reflection.agent import episode_items
from reflection.metrics import summarize
from reflection.mutate import _underperforming

T0 = datetime(2026, 10, 1, tzinfo=UTC)


def _row(i_s: int, pnl: float, *, symbol: str = "BTCUSDT", reason: str = "hit_sl") -> dict:
    return {"strategy_id": "s", "asset_class": "crypto", "symbol": symbol, "side": "long",
            "generated_at": T0 + timedelta(seconds=i_s), "created_at": T0 + timedelta(seconds=i_s),
            "horizon_seconds": 600, "confidence": Decimal("0.6"), "score": Decimal(str(pnl / 10)),
            "pnl_usd": Decimal(str(pnl)), "pnl_pct": Decimal(str(pnl / 1000)), "reason": reason}


def test_one_losing_bet_re_filled_does_not_cross_the_mutation_gate():
    # Twelve re-fills of one losing call: per row n=12 ≥ 10 → mutate.
    rows = [_row(20 * i, -1.0) for i in range(12)]
    m = summarize("s", 1, rows, asset_class="crypto")
    assert (m.n_outcomes, m.n_raw) == (1, 12)
    assert m.total_pnl_usd == Decimal("-12") and m.win_rate == 0
    assert m.by_symbol["BTCUSDT"]["n"] == "1" and m.by_symbol["BTCUSDT"]["n_raw"] == "12"
    assert m.by_reason["hit_sl"]["n"] == "1"
    assert not _underperforming(m, min_outcomes=10)


def test_win_rate_is_per_episode_and_dollars_are_kept():
    rows = [_row(20 * i, -1.0) for i in range(5)]            # one losing bet, 5 rows
    rows += [_row(3600 * k, 2.0, reason="hit_tp") for k in (1, 2, 3)]  # three winning bets
    m = summarize("s", 1, rows)
    assert m.n_outcomes == 4 and m.win_rate == Decimal("0.75")
    assert m.total_pnl_usd == Decimal("1")
    assert m.by_reason["hit_tp"]["avg_pnl_bps"] == "20.0"


def test_recent_outcomes_items_are_bets_newest_first():
    rows = [_row(20 * i, -1.0) for i in range(5)] + [_row(3600, 2.0, reason="hit_tp")]
    items = episode_items(rows)
    assert [it["fills"] for it in items] == [1, 5]
    assert items[1]["pnl_usd"] == -5.0 and items[0]["reason"] == "hit_tp"


def test_probes_are_not_the_policy_and_are_left_out():
    from reflection.metrics import is_probe

    assert is_probe({"is_exploration": True})
    assert not is_probe({"is_exploration": False}) and not is_probe(None) and not is_probe({})


def test_gate_needs_a_significant_loss_not_a_negative_mean():
    # 12 bets, mean -0.5 USD, sd ~2 → upper bound > 0: noise, no mutation.
    noisy = [_row(3600 * k, -0.5 + (2.0 if k % 2 else -2.0)) for k in range(12)]
    m = summarize("s", 1, noisy)
    assert m.n_outcomes == 12 and m.total_pnl_usd < 0
    assert not _underperforming(m, min_outcomes=10)
    # 12 bets that all lose about a dollar → significant → mutate.
    losing = [_row(3600 * k, -1.0 - 0.1 * (k % 3)) for k in range(12)]
    assert _underperforming(summarize("s", 1, losing), min_outcomes=10)


def test_zero_edge_strategy_rarely_crosses_the_gate():
    # Before 2026-10-09 the gate was total < 0 (or avg score < -0.05): a strategy
    # with no edge crossed it about half the time. Now ~5 % (one-sided 95 %).
    import random

    rng = random.Random(4)
    old = new = 0
    trials = 2000
    for _ in range(trials):
        rows = [_row(3600 * k, rng.gauss(0, 1.0)) for k in range(20)]
        m = summarize("s", 1, rows)
        old += m.total_pnl_usd < 0
        new += _underperforming(m, min_outcomes=10)
    assert 0.4 < old / trials < 0.6
    assert new / trials < 0.08
