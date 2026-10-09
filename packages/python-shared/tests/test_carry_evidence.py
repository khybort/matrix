"""Carry evidence: a carry earns `confirmed` on its realised paper episodes,
never on a simulation, through the same promotion bar as every directional row.

Until 2026-10-09 the edge study read only long/short signals, so no carry
could ever be confirmed and `paper_trade._promotion_confirmed` was always False
for one: the book-priced ceiling never lifted and Kelly never applied.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from matrix_shared import edge_study as E
from matrix_shared.promotion import Registry

T0 = datetime(2026, 7, 1, tzinfo=UTC)
NOTIONAL = 1000.0


def _fills(mu: float, *, days: int = 60, per_day: int = 5, day_sd: float = 60.0, sd: float = 120.0,
           seed: int = 0, sid: str = "neg_funding_carry", open_last: int = 0) -> list[dict]:
    """Closed carry episodes, net bps ~ mu + day shock + noise. Each episode is
    its own symbol, so `episode_groups` keeps them apart. Episodes opened the
    same day share a shock: one funding regime, not independent bets."""
    rng = random.Random(seed)
    out = []
    for d in range(days):
        shock = rng.gauss(0.0, day_sd)
        for j in range(per_day):
            at = T0 + timedelta(days=d, minutes=37 * j)
            bps = mu + shock + rng.gauss(0.0, sd)
            closed = not (d == days - 1 and j >= per_day - open_last)
            out.append({
                "strategy_id": sid, "asset_class": "crypto", "symbol": f"C{d}X{j}USDT",
                "side": "inverse_carry", "generated_at": at, "horizon_seconds": 48 * 3600,
                "notional_usd": NOTIONAL, "opened_at": at,
                "closed_at": at + timedelta(hours=10) if closed else None,
                "pnl_usd": NOTIONAL * bps / 10_000 if closed else None,
                "borrow_rate_hourly": None, "borrow_charged_usd": None, "book_close_bps": None,
            })
    return out


def _bar(fills: list[dict], family: int = 13) -> dict:
    rows, returns = E.carry_edge_rows(fills)
    E.apply_promotion_bar(rows, returns, family=family, registry=Registry())
    (row,) = rows
    return row


def test_zero_edge_never_confirms_at_the_books_family_size():
    """200 independent zero-edge carries, 300 episodes over 60 days each:
    none may reach `confirmed`, and none may be sized as `pays`."""
    confirmed = pays = 0
    for seed in range(200):
        row = _bar(_fills(0.0, seed=seed))
        assert row["kind"] == "carry" and row["n"] == 300 and row["n_days"] == 60
        confirmed += row["status"] == "confirmed"
        pays += E.verdict(row, cost_bps=15.0) == "pays"
    assert confirmed == 0 and pays == 0


def test_zero_edge_alone_confirms_no_more_often_than_the_test_size():
    """Even uncorrected for the family (m=1), noise confirms at most at the
    one-sided test size."""
    confirmed = sum(_bar(_fills(0.0, seed=1000 + s), family=1)["status"] == "confirmed" for s in range(200))
    assert confirmed <= 10   # 5 %


def test_day_clustering_is_what_keeps_the_false_positive_rate_honest():
    """Same-day carries share a funding regime. An i.i.d. t over episodes
    rejects a true zero far more often than its nominal 5 %; the day-clustered
    t the carry row reports does not."""
    from matrix_shared.shadow_tracker import clustered_t

    naive = clustered_rej = 0
    n_sims = 300
    for seed in range(n_sims):
        f = _fills(0.0, seed=5000 + seed, day_sd=80.0, sd=80.0, per_day=8, days=30)
        rows, returns = E.carry_edge_rows(f)
        x = returns[("neg_funding_carry", "crypto")]
        # i.i.d. t: every episode its own cluster
        t_iid = clustered_t(x, list(range(len(x)))) or 0.0
        naive += abs(t_iid) >= 1.96
        clustered_rej += rows[0]["p"] < 0.05
    assert naive / n_sims > 0.15
    assert clustered_rej / n_sims < 0.10
    assert clustered_rej < naive


def test_a_real_carry_edge_confirms_and_pays_without_charging_costs_twice():
    row = _bar(_fills(80.0, seed=3))
    assert row["has_edge"] and row["required_n"] is not None and row["n"] >= row["required_n"]
    assert row["dsr"] >= 0.95
    assert row["status"] == "confirmed"
    assert row["net_of_costs"] is True and row["gross_bps"] == row["edge_bps"]
    # The row is already net of fees, book and borrow: a 15 bps round trip is
    # not charged again, so a +20 bps realised carry is not called "unproven".
    assert E.verdict(row, cost_bps=15.0) == "pays"
    assert E.verdict({**row, "gross_bps": 10.0, "edge_bps": 10.0}, cost_bps=15.0) == "pays"
    assert E.verdict({**row, "net_of_costs": False, "gross_bps": 10.0, "edge_bps": 10.0},
                     cost_bps=15.0) == "unproven"


def test_a_real_edge_on_too_few_days_is_not_yet_evidence():
    """300 episodes crammed into ten days are ten funding regimes, not 300."""
    row = _bar(_fills(80.0, days=10, per_day=30, seed=4))
    assert row["n"] == 300 and row["n_days"] == 10 and row["p"] == 1.0
    assert row["t"] == 0.0 and row["t_day"] > 2   # reported, not tested
    assert row["status"] != "confirmed"


def test_open_episodes_are_not_evidence():
    row = _bar(_fills(500.0, days=1, per_day=3, seed=5, open_last=3))
    assert row["n"] == 0 and row["n_open"] == 3 and row["status"] == "unproven"


def test_realised_net_is_the_wallets_pnl_per_episode():
    f = _fills(0.0, days=2, per_day=2, seed=6)
    for r, bps in zip(f, (100.0, -20.0, 40.0, 0.0)):
        r["pnl_usd"] = NOTIONAL * bps / 10_000
    rows, returns = E.carry_edge_rows(f)
    assert [round(x, 6) for x in returns[("neg_funding_carry", "crypto")]] == [100.0, -20.0, 40.0, 0.0]
    assert rows[0]["edge_bps"] == 30.0 and rows[0]["realised_net_bps"] == 30.0


def test_re_entries_inside_one_horizon_are_one_episode():
    f = _fills(0.0, days=1, per_day=1, seed=7)
    again = {**f[0], "generated_at": f[0]["generated_at"] + timedelta(hours=1),
             "opened_at": f[0]["opened_at"] + timedelta(hours=1)}
    rows, _ = E.carry_edge_rows(f + [again])
    assert rows[0]["n"] == 1 and rows[0]["n_raw"] == 2


@pytest.mark.asyncio
async def test_the_study_scores_carries_from_realised_episodes_never_bars(monkeypatch):
    async def no_signals(days, sid):
        return []

    async def carries(days, sid):
        assert days == E.CARRY_DAYS
        return _fills(80.0, seed=8)

    async def no_bars(*a, **k):
        raise AssertionError("a carry must not be simulated on bars")

    monkeypatch.setattr(E, "_load_candidates", no_signals)
    monkeypatch.setattr(E, "_load_carry_fills", carries)
    monkeypatch.setattr(E, "_load_bars", no_bars)
    monkeypatch.setattr(E.Registry, "load", classmethod(lambda cls, path=None: cls()))
    monkeypatch.setattr(E.Registry, "save", lambda self, path=None: None)
    (row,) = await E.run_edge_study(days=14, strategy_id="neg_funding_carry", family=13)
    assert row["kind"] == "carry" and row["family"] == 13
    assert row["status"] == "confirmed"


def test_champion_and_shadow_challenger_are_not_pooled():
    """inverse_carry runs an active v1 and a shadow v2 under one strategy_id.
    A winning challenger must not confirm the champion's (losing) book, and
    the two arms' fills on one symbol must not merge into one episode."""
    champion = _fills(-20.0, seed=5, sid="inverse_carry")
    challenger = [{**r, "is_shadow": True} for r in _fills(400.0, seed=6, sid="inverse_carry")]
    for r in champion:
        r["is_shadow"] = False
    rows, returns = E.carry_edge_rows(champion + challenger)
    (row,) = rows
    assert row["n"] == 300 and row["edge_bps"] < 50   # pooled: one merged episode per symbol, ~+190
    assert row["arm"] == "champion"
    E.apply_promotion_bar(rows, returns, family=13, registry=Registry())
    assert row["status"] != "confirmed"

    # Shadow only (neg_funding_carry trades nowhere else): judged on the shadow book.
    (only,), _ = E.carry_edge_rows(challenger)
    assert only["arm"] == "shadow" and only["n"] == 300


@pytest.mark.asyncio
async def test_carry_closes_under_the_old_funding_accounting_are_not_evidence(monkeypatch):
    """Before the per-settlement funding fix went live (2026-10-09 13:39 UTC)
    every inverse/xexch carry exited on Bybit's post-settlement placeholder at
    the four-leg fee: those closes measure the bug. Only neg_funding_carry had
    a band `since`, so inverse_carry's pre-fix closes entered its evidence.
    A later band `since` still wins; open positions and positions opened before
    but closed after the fix (booked end to end by the new code) are kept."""
    from contextlib import asynccontextmanager

    from matrix_shared import shadow_tracker

    fix = datetime(2026, 10, 9, 13, 39, 14, tzinfo=UTC)  # carry_funding went live (7d7b854)

    def fill(sid, sym, opened, closed):
        return {"strategy_id": sid, "asset_class": "crypto", "symbol": sym, "side": "inverse_carry",
                "generated_at": opened, "opened_at": opened, "closed_at": closed}

    h = timedelta(hours=1)
    rows = [
        fill("inverse_carry", "OLDUSDT", fix - 30 * h, fix - 29 * h),       # placeholder flip, pre-fix
        fill("inverse_carry", "EDGEUSDT", fix - 2 * h, fix - timedelta(seconds=1)),
        fill("inverse_carry", "SPANUSDT", fix - 2 * h, fix + 6 * h),         # opened before, closed after
        fill("inverse_carry", "NEWUSDT", fix + h, fix + 9 * h),
        fill("inverse_carry", "OPENUSDT", fix - 3 * h, None),
        fill("neg_funding_carry", "NFCAUSDT", fix + h, fix + 5 * h),         # before its band since
        fill("neg_funding_carry", "NFCBUSDT", fix + 3 * h, fix + 8 * h),
    ]

    class _Result:
        def mappings(self):
            return self

        def all(self):
            return rows

    class _Session:
        async def execute(self, *a, **k):
            return _Result()

    @asynccontextmanager
    async def fake_scope():
        yield _Session()

    async def bands():
        return {("neg_funding_carry", "crypto"): {"since": (fix + 2 * h).isoformat()}}

    monkeypatch.setattr(E, "shared_session_scope", fake_scope)
    monkeypatch.setattr(shadow_tracker, "load_bands", bands)
    kept = {r["symbol"] for r in await E._load_carry_fills(90, None)}
    assert kept == {"SPANUSDT", "NEWUSDT", "OPENUSDT", "NFCBUSDT"}
    assert E.CARRY_EVIDENCE_SINCE == fix  # the default (MATRIX_EDGE_CARRY_EVIDENCE_SINCE)

    # Configurable: an earlier cutoff lets the pre-fix closes back in.
    monkeypatch.setattr(E, "CARRY_EVIDENCE_SINCE", fix - 40 * h)
    kept = {r["symbol"] for r in await E._load_carry_fills(90, None)}
    assert {"OLDUSDT", "EDGEUSDT"} <= kept and "NFCAUSDT" not in kept
