"""Decision-driving consumers count one bet once (edge_study.episode_groups):
setup memory's neighbours, the pair edge behind EV ranking and sizing, and the
per-key summary the lesson synthesizer and reflection read."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from matrix_shared import setup_memory as SM
from matrix_shared.allocation import pair_episode_edges, shrink_pair_edge
from matrix_shared.edge_study import episode_summary

T0 = datetime(2026, 10, 1, tzinfo=UTC)


def _row(i_s: int, symbol: str = "BTCUSDT", side: str = "long", **kw) -> dict:
    return {"strategy_id": "s", "asset_class": "crypto", "symbol": symbol, "side": side,
            "generated_at": T0 + timedelta(seconds=i_s), "horizon_seconds": 600, **kw}


def test_episode_summary_counts_a_re_emitted_bet_once_and_keeps_its_dollars():
    # One losing bet re-filled five times inside its horizon, one winning bet later.
    rows = [_row(30 * i, pnl_usd=-1.0) for i in range(5)] + [_row(3600, pnl_usd=2.0)]
    s = episode_summary(list(reversed(rows)), "symbol")["BTCUSDT"]
    assert s == {"n": 2, "n_raw": 6, "wins": 1, "sum": -3.0}


def test_episode_summary_key_comes_from_the_bet():
    rows = [_row(0, regime="a", pnl_usd=1.0), _row(30, regime="b", pnl_usd=1.0),
            _row(0, side="short", regime="b", pnl_usd=-1.0)]
    out = episode_summary(rows, lambda r: (r["regime"], r["side"]))
    assert out == {("a", "long"): {"n": 1, "n_raw": 2, "wins": 1, "sum": 2.0},
                   ("b", "short"): {"n": 1, "n_raw": 1, "wins": 0, "sum": -1.0}}


def test_setup_memory_neighbours_are_episodes():
    feats = {"buy_share_60s": 0.9, "regime": "high/up/pos"}
    # Ten re-fills of one winning bet, then ten independent losing bets.
    rows = [_row(10 * i, features=feats, pnl_pct=0.01) for i in range(10)]
    rows += [_row(3600 * (i + 1), features=feats, pnl_pct=-0.01) for i in range(10)]
    eps = SM.episode_rows(rows)
    assert len(eps) == 11
    st = SM.summarize(eps, SM.setup_vector(feats), k=20)
    assert (st.n, st.wins) == (11, 1)
    # Per row the same history read as a coin flip (10 of 20).
    raw = [(SM.setup_vector(r["features"]), r["pnl_pct"]) for r in rows]
    assert SM.summarize(raw, SM.setup_vector(feats), k=20).wins == 10


def test_pair_edge_shrinks_on_episodes():
    rows = [_row(10 * i, pnl_usd=1.0, notional_usd=100.0) for i in range(10)]
    edges = pair_episode_edges(rows)
    # Ten re-fills of one +1% bet are one sample, not ten.
    assert edges[("s", "BTCUSDT")] == shrink_pair_edge(1, 0.01)
    assert edges[("s", "BTCUSDT")] < shrink_pair_edge(10, 0.01)
