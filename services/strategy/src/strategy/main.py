"""Strategy generator loop.

Each tick the dispatcher walks every registered `MarketAdapter` and, while
that market's session is open, runs every strategy class registered for
it. Drafts go to predictions table; paper-trade engine consumes from there.

Strategies declare their market via the `market` ClassVar. They no longer
self-gate on session hours — `MarketAdapter.is_session_open()` does that.

Usage:
    uv run python -m strategy.main                    # default 30s loop
    uv run python -m strategy.main --interval 10      # faster loop
    uv run python -m strategy.main --once             # one cycle and exit
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
import time

from loguru import logger

from matrix_shared import shared_session_scope
from matrix_shared.markets import MarketAdapter, all_markets
from matrix_shared.models import StrategyConfig
from sqlalchemy import select

from strategy.base import PredictionDraft
from strategy.lessons import filter_drafts
from strategy.modules import STRATEGIES_BY_MARKET
from strategy.persist import persist_drafts

DEFAULT_INTERVAL_S = 30.0

# strategy_configs DB gate — re-read every this many seconds so an operator
# can disable a strategy at runtime without restarting the container.
_CONFIG_TTL_S = 60.0
_active_ids_cache: set[str] | None = None
_active_ids_ts: float = 0.0


async def _active_strategy_ids() -> set[str]:
    """Return the set of strategy_ids that have an active strategy_configs row.

    Returns None-sentinel (all strategies pass) when the table has NO rows at
    all — this handles the bootstrap state before any strategy is ever seeded.
    The set is cached for _CONFIG_TTL_S to avoid a DB round-trip every tick.
    """
    global _active_ids_cache, _active_ids_ts
    now = time.monotonic()
    if _active_ids_cache is not None and now - _active_ids_ts < _CONFIG_TTL_S:
        return _active_ids_cache

    async with shared_session_scope() as session:
        rows = (await session.execute(
            select(StrategyConfig.strategy_id)
            .where(StrategyConfig.status == "active")
        )).scalars().all()

    if not rows:
        # No rows at all → bootstrap; let everything through so the first
        # run seeds outcomes without requiring a DB migration step.
        _active_ids_cache = set()
        _active_ids_ts = now
        return set()

    _active_ids_cache = set(rows)
    _active_ids_ts = now
    return _active_ids_cache


def _instantiate_for_market(market: MarketAdapter) -> list:
    """Build a fresh list of strategy instances for `market` from the registry.

    A KeyError here means a market was registered as a MarketAdapter but has
    no `modules/<market>/__init__.py` exporting STRATEGIES — surface loudly.
    """
    if market.name not in STRATEGIES_BY_MARKET:
        logger.warning(
            f"market {market.name!r} has no registered strategies "
            f"(STRATEGIES_BY_MARKET keys: {sorted(STRATEGIES_BY_MARKET)})"
        )
        return []
    return [cls() for cls in STRATEGIES_BY_MARKET[market.name]]


async def _tick() -> int:
    active_ids = await _active_strategy_ids()
    drafts: list[PredictionDraft] = []
    for market in all_markets():
        if not market.is_session_open():
            logger.debug(f"market {market.name} session closed; skip")
            continue
        for strat in _instantiate_for_market(market):
            # Gate: skip strategies that have been explicitly retired in
            # strategy_configs. An empty active_ids set means "bootstrap mode —
            # no rows yet, let everything through."
            if active_ids and strat.id not in active_ids:
                logger.debug(f"strategy {strat.id}: no active config row; skip")
                continue
            try:
                ds = await strat.generate()
                drafts.extend(ds)
            except Exception as e:
                logger.exception(f"strategy {strat.id} ({market.name}) failed: {e}")

    # Data-driven filter: drop candidates that match an active 'avoid' lesson.
    # Keeps strategy modules symbol-agnostic; lessons are produced by the
    # lessons feeder and can be added/removed without code changes.
    drafts = await filter_drafts(drafts)
    return await persist_drafts(drafts)


async def run(interval_s: float) -> None:
    stop = asyncio.Event()

    def _handle_signal(*_: object) -> None:
        logger.info("shutdown signal received")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    while not stop.is_set():
        try:
            n = await _tick()
            logger.info(f"tick: {n} new predictions")
        except Exception as e:
            logger.exception(f"tick error: {e}")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except TimeoutError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Matrix strategy generator")
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL_S,
        help=f"Generator loop interval seconds (default {DEFAULT_INTERVAL_S})",
    )
    parser.add_argument(
        "--once", action="store_true", help="Run one tick and exit"
    )
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level: <5} | {message}")
    logger.info(
        f"strategy start: interval={args.interval}s once={args.once} "
        f"markets={[m.name for m in all_markets()]}"
    )

    if args.once:
        asyncio.run(_tick())
    else:
        asyncio.run(run(args.interval))


if __name__ == "__main__":
    main()
