"""matrix_shared.retention — policy windows + bounded pruning.

The integration test writes synthetic rows for a test-only symbol into the
LOCAL market_trades table and prunes them; live symbols are untouched
because pruning is scoped to the partition key.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from matrix_shared.retention import POLICIES, Policy, prune_once, prune_policy

pytestmark = pytest.mark.asyncio

TEST_SYM = "RETENTIONTESTUSDT"


def test_policy_days_env_override_and_cutoff(monkeypatch):
    p = Policy("market_trades", "trade_ts", 7, "local")
    monkeypatch.delenv(p.env_key, raising=False)
    assert p.days() == 7
    monkeypatch.setenv(p.env_key, "3")
    assert p.days() == 3
    now = datetime(2026, 9, 12, tzinfo=timezone.utc)
    assert p.cutoff(now) == now - timedelta(days=3)
    monkeypatch.setenv(p.env_key, "garbage")
    assert p.days() == 7


def test_policies_cover_the_unbounded_tables():
    tables = {p.table for p in POLICIES}
    assert {"market_trades", "market_orderbook_snapshots", "market_ticker_snapshots",
            "wallet_snapshots"} <= tables
    for p in POLICIES:
        assert p.default_days >= 2, p.table  # never below the widest live lookback


async def _seed_trades(n_old: int, n_new: int) -> None:
    from matrix_shared.db import local_session_scope
    now = datetime.now(timezone.utc)
    async with local_session_scope() as session:
        await session.execute(text("DELETE FROM market_trades WHERE symbol = :s"), {"s": TEST_SYM})
        for i in range(n_old + n_new):
            ts = now - timedelta(days=30) if i < n_old else now
            await session.execute(text(
                "INSERT INTO market_trades (id, exchange, exchange_trade_id, symbol, trade_ts, "
                " side, price, size) VALUES (:id, 'test', :tid, :s, :ts, 'buy', 1, 1)"
            ), {"id": uuid.uuid4(), "tid": f"ret-{uuid.uuid4().hex}", "s": TEST_SYM, "ts": ts})


async def _count() -> int:
    from matrix_shared.db import local_session_scope
    async with local_session_scope() as session:
        return (await session.execute(
            text("SELECT count(*) FROM market_trades WHERE symbol = :s"), {"s": TEST_SYM}
        )).scalar_one()


@pytest.mark.skipif(not os.environ.get("LOCAL_DATABASE_URL"), reason="needs local DB")
async def test_prune_policy_deletes_only_old_rows_in_batches(monkeypatch):
    await _seed_trades(n_old=25, n_new=5)
    try:
        # Restrict the partition keys to our symbol so the test is fast and
        # doesn't touch live rows.
        import matrix_shared.retention as r
        async def _keys(_p):
            return [TEST_SYM]
        monkeypatch.setattr(r, "_partition_keys", _keys)
        policy = Policy("market_trades", "trade_ts", 7, "local")
        deleted = await prune_policy(policy, batch=10)
        assert deleted == 25
        assert await _count() == 5
        # idempotent
        assert await prune_policy(policy, batch=10) == 0
    finally:
        from matrix_shared.db import local_session_scope
        async with local_session_scope() as session:
            await session.execute(text("DELETE FROM market_trades WHERE symbol = :s"), {"s": TEST_SYM})


async def test_prune_once_respects_budget_and_reports(monkeypatch):
    import matrix_shared.retention as r
    calls: list[str] = []

    async def fake_prune(policy, *, batch, deadline, now=None):
        calls.append(policy.table)
        return 3

    monkeypatch.setattr(r, "prune_policy", fake_prune)
    report = await prune_once(budget_s=5)
    assert set(report) == {p.table for p in POLICIES}
    assert all(v == 3 for v in report.values())
    # Zero budget → nothing runs.
    calls.clear()
    report = await prune_once(budget_s=0)
    assert report == {} and calls == []


async def test_prune_uses_the_index_and_not_a_sequential_scan():
    """The pruner's inner SELECT must be orderable by the timestamp column, or
    Postgres answers a LIMIT over a huge estimated match set with a sequential
    scan. For a symbol with nothing old left the LIMIT never fills, so the scan
    reads the whole table and one query consumes the entire budget — which is
    how retention ran for months, logging a couple of hundred rows every five
    minutes while `market_trades` kept data back to June.

    Asserted against the planner itself, because this is a plan bug, not a
    logic bug: the Python is identical either way.
    """
    import asyncpg

    dsn = os.environ.get("LOCAL_DATABASE_URL", "")
    if not dsn:
        pytest.skip("LOCAL_DATABASE_URL unset")
    # Its own connection on purpose: the shared engine is bound to whichever
    # event loop created it, and this assertion is about the planner, not about
    # session plumbing.
    conn = await asyncpg.connect(dsn.replace("postgres://", "postgresql://"))
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        absent = f"NOSUCH{uuid.uuid4().hex[:8].upper()}USDT"
        rows = await conn.fetch(
            "EXPLAIN SELECT ctid FROM market_trades "
            "WHERE trade_ts < $1 AND symbol = $2 "
            "ORDER BY trade_ts LIMIT 10000",
            cutoff, absent,
        )
        plan = "\n".join(r[0] for r in rows)
    finally:
        await conn.close()
    assert "Index Scan" in plan, plan
    assert "Seq Scan" not in plan, plan


async def test_the_shipped_statement_orders_by_the_policy_timestamp():
    """Guard the actual SQL the module builds, so a future edit cannot drop the
    ORDER BY and silently reintroduce the sequential scan."""
    import inspect

    from matrix_shared import retention as R

    src = inspect.getsource(R.prune_policy)
    assert "ORDER BY {policy.ts_col}" in src


def test_market_trades_is_swept_globally_not_per_symbol():
    """Per-symbol pruning reads an append-only, symbol-interleaved table in the
    worst possible order: one symbol's oldest rows are scattered over the whole
    heap. Sweeping in time order takes the same rows from contiguous pages —
    measured 2026-09-20 as a thousandfold difference, and the reason retention
    lost to ingestion for months."""
    trades = next(p for p in POLICIES if p.table == "market_trades")
    assert trades.partition_col is None


async def test_the_global_sweep_has_an_index_to_stand_on():
    """Without `(trade_ts)` the ordered global sweep degenerates into a sort of
    the whole table the moment the backlog is gone."""
    import asyncpg

    dsn = os.environ.get("LOCAL_DATABASE_URL", "")
    if not dsn:
        pytest.skip("LOCAL_DATABASE_URL unset")
    conn = await asyncpg.connect(dsn.replace("postgres://", "postgresql://"))
    try:
        # `indisvalid`, not merely present: CREATE INDEX CONCURRENTLY writes the
        # catalog row long before the index can be used, so a presence check
        # passes against an index the planner will ignore.
        rows = await conn.fetch(
            "SELECT i.indisvalid FROM pg_class c JOIN pg_index i ON i.indexrelid = c.oid "
            "WHERE c.relname = 'ix_market_trades_ts'"
        )
    finally:
        await conn.close()
    assert rows, "migration 0039 has not been applied to this database"
    assert rows[0]["indisvalid"], "ix_market_trades_ts exists but is not valid yet"
