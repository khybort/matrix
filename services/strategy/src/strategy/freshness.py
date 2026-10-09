"""Stand down on stale data: no prediction for a symbol whose prices are frozen.

During the 2026-09-30 → 10-06 VM network outage every feed stopped, yet the
strategies kept emitting on the last prices they had (dca 72/day, BIST
144/day): signals computed on data days old, carrying an `entry_price_ref`
the market had long left. A draft is persisted only when its symbol's newest
datum is younger than its market's threshold. Thresholds are a few times the
measured feed lag (2026-10-09): crypto tickers median 4 s / p90 46 s, BIST 1m
bars arrive ~16 min late (p90 20 min). BIST and US are only dispatched while
their session is open, so an overnight gap never reaches this check.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

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
_LOOKBACK = timedelta(days=1)


def drop_stale(
    drafts: Sequence[PredictionDraft], latest: dict[tuple[str, str], datetime], now: datetime
) -> list[PredictionDraft]:
    """Keep drafts whose (asset_class, symbol) has a datum within the market's
    threshold. A market without a threshold passes; a symbol with no datum at
    all is stale."""
    kept: list[PredictionDraft] = []
    for d in drafts:
        limit = STALE_AFTER.get(d.asset_class)
        if limit is None:
            kept.append(d)
            continue
        ts = latest.get((d.asset_class, d.symbol))
        if ts is not None and now - ts <= limit:
            kept.append(d)
    return kept


async def _latest(drafts: Sequence[PredictionDraft], now: datetime) -> dict[tuple[str, str], datetime]:
    by_class: dict[str, set[str]] = {}
    for d in drafts:
        if d.asset_class in STALE_AFTER:
            by_class.setdefault(d.asset_class, set()).add(d.symbol)
    out: dict[tuple[str, str], datetime] = {}
    since = now - _LOOKBACK

    def _keep(ac: str, sym: str, ts: datetime | None) -> None:
        if ts is not None and ts > out.get((ac, sym), datetime.min.replace(tzinfo=UTC)):
            out[(ac, sym)] = ts

    async with local_session_scope() as session:
        for ac, syms in by_class.items():
            # (asset_class, symbol, interval, ts) unique index serves this.
            rows = await session.execute(
                text(
                    "SELECT symbol, max(ts) FROM market_bars WHERE asset_class = :ac "
                    "AND interval = '1m' AND symbol = ANY(:syms) AND ts > :since GROUP BY symbol"
                ),
                {"ac": ac, "syms": list(syms), "since": since},
            )
            for sym, ts in rows:
                _keep(ac, sym, ts + _BAR if ts is not None else None)
            if ac == "crypto":
                # Funding/carry modules read the ticker stream, not bars.
                rows = await session.execute(
                    text(
                        "SELECT symbol, max(snapshot_ts) FROM market_ticker_snapshots "
                        "WHERE symbol = ANY(:syms) AND snapshot_ts > :since GROUP BY symbol"
                    ),
                    {"syms": list(syms), "since": since},
                )
                for sym, ts in rows:
                    _keep(ac, sym, ts)
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
