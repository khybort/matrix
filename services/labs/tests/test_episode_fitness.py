"""episode_stats: lab fitness counts a re-emitted bet once."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from labs.evaluate import compute_fitness, episode_stats

EXP = uuid.uuid4()
T0 = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)


def _ev(sec: int, score: str, *, symbol: str = "BTCUSDT", side: str = "long", horizon: int = 600) -> dict:
    at = T0 + timedelta(seconds=sec)
    return {
        "experiment_id": EXP, "asset_class": "crypto", "symbol": symbol, "side": side,
        "generated_at": at, "close_at": at + timedelta(seconds=horizon),
        "score": Decimal(score), "pnl_pct": Decimal(score) / 100,
    }


def test_reemissions_inside_the_horizon_are_one_episode():
    # The pre-2026-09-13 20 s tick: the same bet opened every tick while the
    # first was still open. Thirty rows, one bet.
    rows = [_ev(20 * i, "1") for i in range(30)]
    st = episode_stats(rows)
    assert (st.n, st.n_raw, st.n_wins) == (1, 30, 1)
    assert st.total_score == Decimal("1")


def test_episode_value_is_the_first_evaluation_not_the_luckiest():
    rows = [_ev(0, "-0.5"), _ev(20, "1"), _ev(40, "1")]
    st = episode_stats(rows)
    assert st.n == 1 and st.n_wins == 0 and st.total_score == Decimal("-0.5")


def test_sequential_bets_symbols_and_sides_are_separate():
    rows = [
        _ev(0, "0.2"),
        _ev(600, "0.4"),  # the first bet's horizon has ended
        _ev(10, "-0.1", symbol="ETHUSDT"),
        _ev(30, "0.3", side="short"),
    ]
    st = episode_stats(rows)
    assert (st.n, st.n_raw, st.n_wins) == (4, 4, 3)
    assert st.total_score == Decimal("0.8")


def test_order_of_input_does_not_matter():
    rows = [_ev(20 * i, str(i % 3 - 1)) for i in range(40)] + [_ev(900, "0.5")]
    assert episode_stats(rows) == episode_stats(list(reversed(rows)))


def test_fitness_uses_episode_n_and_std():
    # Two independent bets, each re-emitted ten times. Per row this was n=20
    # with a tiny std; per episode it is n=2.
    rows = [_ev(20 * i, "0.6") for i in range(10)] + [_ev(1000 + 20 * i, "0.2") for i in range(10)]
    st = episode_stats(rows)
    assert st.n == 2
    assert st.std > 0
    assert st.fitness == compute_fitness(n=2, mean=Decimal("0.4"), std=st.std)
    rows_as_samples = compute_fitness(n=20, mean=Decimal("0.4"), std=Decimal("0.2052"))
    assert st.fitness < rows_as_samples


def test_empty():
    st = episode_stats([])
    assert (st.n, st.n_raw, st.fitness) == (0, 0, Decimal("0"))
