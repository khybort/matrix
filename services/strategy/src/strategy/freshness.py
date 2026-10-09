"""Stand down on stale data, and stamp every draft with the price at emission.

During the 2026-09-30 → 10-06 VM network outage every feed stopped, yet the
strategies kept emitting on the last prices they had (dca 72/day, BIST
144/day): signals computed on data days old, carrying an `entry_price_ref`
the market had long left. A draft is persisted only when its symbol has a
datum younger than its market's threshold. Thresholds are a few times the
measured feed lag (2026-10-09): crypto tickers median 4 s / p90 46 s, BIST 1m
bars arrive ~16 min late (p90 20 min). BIST and US are only dispatched while
their session is open, so an overnight gap never reaches this check.

The same datum becomes the draft's `entry_price_ref`. Modules picked their
own reference and several picked a stale one: dca took the last *trade* of a
symbol with no trade stream, on average 3.3 days old (2026-10-09), so its
reference sat +38…+314 bps away from the bar at `generated_at` and corrupted
EV, TP/SL anchors and every study that reads it. The module's own value is
kept in `context.module_price_ref` for audit.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from loguru import logger
from sqlalchemy import text

from matrix_shared import local_session_scope

from strategy.base import PredictionDraft

STALE_AFTER: dict[str, timedelta] = {
    "crypto": timedelta(minutes=3),
    "bist": timedelta(minutes=45),
    "us": timedelta(minutes=30),
}
_BAR = timedelta(minutes=1)  # 1m bar `ts` is its start; the datum is its close

Quote = tuple[datetime, Decimal]


def drop_stale(
    drafts: Sequence[PredictionDraft], latest: dict[tuple[str, str], Quote], now: datetime
) -> list[PredictionDraft]:
    """Keep drafts whose (asset_class, symbol) has a quote within the market's
    threshold, re-anchored to that quote's price. A market without a
    threshold passes untouched; a symbol with no quote at all is stale."""
    kept: list[PredictionDraft] = []
    for d in drafts:
        limit = STALE_AFTER.get(d.asset_class)
        if limit is None:
            kept.append(d)
            continue
        q = latest.get((d.asset_class, d.symbol))
        if q is None or now - q[0] > limit:
            continue
        if d.entry_price_ref != q[1]:
            d.context = {**(d.context or {}), "module_price_ref": str(d.entry_price_ref)}
            d.entry_price_ref = q[1]
        kept.append(d)
    return kept


async def _latest(drafts: Sequence[PredictionDraft], now: datetime) -> dict[tuple[str, str], Quote]:
    """Freshest (ts, price) per (asset_class, symbol) inside the threshold:
    crypto from trades and the ticker stream, every market from 1m bars."""
    by_class: dict[str, set[str]] = {}
    for d in drafts:
        if d.asset_class in STALE_AFTER:
            by_class.setdefault(d.asset_class, set()).add(d.symbol)
    out: dict[tuple[str, str], Quote] = {}

    def _keep(ac: str, sym: str, ts: datetime, px) -> None:
        if px is None or px <= 0:
            return
        cur = out.get((ac, sym))
        if cur is None or ts > cur[0]:
            out[(ac, sym)] = (ts, Decimal(px))

    async with local_session_scope() as session:
        for ac, syms in by_class.items():
            since = now - STALE_AFTER[ac]
            params = {"ac": ac, "syms": list(syms), "since": since, "bar_since": since - _BAR}
            # (asset_class, symbol, interval, ts) unique index serves this.
            rows = await session.execute(
                text(
                    "SELECT DISTINCT ON (symbol) symbol, ts, close FROM market_bars "
                    "WHERE asset_class = :ac AND interval = '1m' AND symbol = ANY(:syms) "
                    "AND ts > :bar_since ORDER BY symbol, ts DESC"
                ),
                params,
            )
            for sym, ts, px in rows:
                _keep(ac, sym, ts + _BAR, px)
            if ac != "crypto":
                continue
            # (symbol, trade_ts) and (symbol, snapshot_ts) indexes serve these.
            for sql in (
                "SELECT DISTINCT ON (symbol) symbol, trade_ts, price FROM market_trades "
                "WHERE symbol = ANY(:syms) AND trade_ts > :since ORDER BY symbol, trade_ts DESC",
                "SELECT DISTINCT ON (symbol) symbol, snapshot_ts, coalesce(last_price, mark_price) "
                "FROM market_ticker_snapshots WHERE symbol = ANY(:syms) AND snapshot_ts > :since "
                "AND exchange = 'bybit' ORDER BY symbol, snapshot_ts DESC",
            ):
                for sym, ts, px in await session.execute(text(sql), params):
                    _keep(ac, sym, ts, px)
    return out


async def apply_freshness_guard(drafts: Sequence[PredictionDraft]) -> list[PredictionDraft]:
    if not drafts:
        return []
    now = datetime.now(UTC)
    kept = drop_stale(drafts, await _latest(drafts, now), now)
    if len(kept) < len(drafts):
        dropped = sorted({f"{d.asset_class}:{d.symbol}" for d in drafts} - {f"{d.asset_class}:{d.symbol}" for d in kept})
        logger.warning(f"stale-data stand-down: dropped {len(drafts) - len(kept)} draft(s) on {dropped[:10]}")
    return kept
