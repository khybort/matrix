"""Time-based retention for append-only telemetry tables.

Nothing pruned anything before 2026-09-12: `market_trades` reached 302M rows
(85 GB) and `wallet_snapshots` was written every 5s. This module is the
single owner of "how long do we keep raw data".

Design:
  * Windows are generous relative to every reader (grid band 24h, replayer
    7d, universe edge 14d, dashboard snapshots 200 rows) — see the table.
  * Deletes are bounded: per (table, symbol) batches through the existing
    `(symbol, ts)` indexes, inside a wall-clock budget, so a tick never
    holds a long lock and the 300M-row backlog drains over hours instead of
    one giant transaction.
  * Space is returned to Postgres' free-space map by autovacuum, not to the
    OS — `make db-compact TABLE=…` (VACUUM FULL, operator-run, locks) does that.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import text

from matrix_shared.db import local_session_scope, shared_session_scope


@dataclass(frozen=True, slots=True)
class Policy:
    table: str
    ts_col: str
    default_days: float
    tier: str  # "local" | "shared"
    partition_col: str | None = "symbol"  # column to batch on (indexed with ts_col)

    @property
    def env_key(self) -> str:
        return f"MATRIX_RETENTION_{self.table.upper()}_DAYS"

    def days(self) -> float:
        raw = os.environ.get(self.env_key, "").strip()
        try:
            return float(raw) if raw else self.default_days
        except ValueError:
            logger.warning(f"retention: bad {self.env_key}={raw!r}; using {self.default_days}")
            return self.default_days

    def cutoff(self, now: datetime | None = None) -> datetime:
        now = now or datetime.now(timezone.utc)
        return now - timedelta(days=self.days())


POLICIES: tuple[Policy, ...] = (
    # Raw tick prints: readers look back ≤24h live, replayer ≤7d.
    #
    # Swept GLOBALLY (partition_col=None), not per symbol. This table is
    # append-only with ~900 symbols interleaved, so one symbol's oldest rows
    # are scattered across the whole heap: deleting 10 000 of them touches
    # 10 000 cold pages and measured three to six minutes a batch, which is
    # why retention lost to ingestion for months. The same 10 000 rows taken
    # in time order come from ~180 contiguous pages — measured 2026-09-20 at
    # 20 000 rows in 1.1-2.1 s, about a thousandfold. The `(trade_ts)` index
    # added alongside this keeps the ordered sweep cheap once the backlog is
    # gone and the predicate stops matching anything.
    Policy("market_trades", "trade_ts", 7, "local", partition_col=None),
    # L2 snapshots: no reader beyond the model; keep 2d for debugging.
    Policy("market_orderbook_snapshots", "snapshot_ts", 2, "local"),
    # Funding / OI / mark: small rows, 30d covers every lookback.
    Policy("market_ticker_snapshots", "snapshot_ts", 30, "local"),
    # Equity curve: dashboard reads the newest 200; trailing stop uses today.
    Policy("wallet_snapshots", "snapshot_ts", 30, "shared", partition_col="wallet_id"),
)

# 10k, not 50k: the budget is only checked between batches and one cold 50k
# `DELETE ... WHERE ctid IN (...)` on market_trades ran > 45 s of DataFileRead.
DEFAULT_BATCH = int(os.environ.get("MATRIX_RETENTION_BATCH", "10000"))
DEFAULT_BUDGET_S = float(os.environ.get("MATRIX_RETENTION_BUDGET_S", "45"))


def _scope(tier: str):
    return local_session_scope if tier == "local" else shared_session_scope


async def _partition_keys(policy: Policy) -> list[str]:
    """Values of `partition_col` to iterate. Cheap sources only — never a
    DISTINCT over the big table itself."""
    if policy.partition_col == "wallet_id":
        async with shared_session_scope() as session:
            rows = (await session.execute(text("SELECT id::text FROM wallets"))).all()
        return [r[0] for r in rows]
    keys: set[str] = set()
    try:
        async with shared_session_scope() as session:
            rows = (await session.execute(text("SELECT symbol FROM tradable_symbols"))).all()
        keys.update(r[0] for r in rows)
    except Exception as e:  # noqa: BLE001 — table may not exist on a fresh node
        logger.debug(f"retention: tradable_symbols unavailable ({e})")
    async with local_session_scope() as session:
        rows = (await session.execute(text(
            "SELECT DISTINCT symbol FROM market_bars WHERE asset_class = 'crypto'"
        ))).all()
    keys.update(r[0] for r in rows)
    return sorted(keys)


async def prune_policy(
    policy: Policy,
    *,
    batch: int = DEFAULT_BATCH,
    deadline: float | None = None,
    now: datetime | None = None,
) -> int:
    """Delete rows older than the policy cutoff in bounded batches.

    Returns rows deleted. Stops early (returns partial count) when the
    monotonic `deadline` passes — the next tick resumes where it left off
    because deletion is idempotent on the cutoff predicate.
    """
    cutoff = policy.cutoff(now)
    deleted = 0
    col = policy.partition_col
    scope = _scope(policy.tier)
    keys = await _partition_keys(policy) if col else [None]
    for key in keys:
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                return deleted
            where = f"{policy.ts_col} < :cutoff"
            params: dict = {"cutoff": cutoff, "lim": batch}
            if col:
                where += f" AND {col} = CAST(:key AS {'uuid' if col == 'wallet_id' else 'text'})"
                params["key"] = key
            # ORDER BY the timestamp is not cosmetic — it is the difference
            # between this module working and this module lying. Without it the
            # planner sees `LIMIT` over a predicate it estimates as tens of
            # millions of rows and picks a sequential scan; for a symbol with
            # nothing old left the LIMIT never fills, so the scan runs the whole
            # 300M-row table and one query eats the entire budget. Measured on
            # 2026-09-20: 0.9 ms with the ORDER BY against minutes without it,
            # and the backlog had sat undrained since June while retention
            # logged a couple of hundred rows every five minutes and looked fine.
            sql = text(
                f"DELETE FROM {policy.table} WHERE ctid IN ("
                f"  SELECT ctid FROM {policy.table} WHERE {where} "
                f"  ORDER BY {policy.ts_col} LIMIT :lim)"
            )
            async with scope() as session:
                result = await session.execute(sql, params)
                n = result.rowcount or 0
            deleted += n
            if n < batch:
                break
    return deleted


async def prune_once(
    *,
    policies: tuple[Policy, ...] = POLICIES,
    batch: int = DEFAULT_BATCH,
    budget_s: float = DEFAULT_BUDGET_S,
) -> dict[str, int]:
    """Run every policy within one wall-clock budget. Safe to call often."""
    deadline = time.monotonic() + budget_s
    report: dict[str, int] = {}
    for policy in policies:
        if time.monotonic() >= deadline:
            break
        try:
            report[policy.table] = await prune_policy(policy, batch=batch, deadline=deadline)
        except Exception as e:  # noqa: BLE001 — one table's failure must not stop the rest
            logger.exception(f"retention: {policy.table} prune failed: {e}")
            report[policy.table] = -1
    if any(v for v in report.values()):
        logger.info("retention: pruned " + ", ".join(f"{k}={v}" for k, v in report.items()))
    return report


async def drain(*, batch: int = DEFAULT_BATCH, max_minutes: float = 0) -> dict[str, int]:
    """Operator one-shot: keep pruning until nothing is left (or max_minutes)."""
    started = time.monotonic()
    total: dict[str, int] = {}
    while True:
        report = await prune_once(batch=batch, budget_s=60)
        for k, v in report.items():
            total[k] = total.get(k, 0) + max(v, 0)
        if not any(v > 0 for v in report.values()):
            return total
        if max_minutes and (time.monotonic() - started) / 60 >= max_minutes:
            return total
