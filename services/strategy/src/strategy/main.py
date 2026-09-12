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
from matrix_shared.markets.crypto import crypto_universe_async
from matrix_shared.models import StrategyConfig
from sqlalchemy import select

from strategy.base import PredictionDraft
from strategy.lessons import filter_drafts
from strategy.modules import STRATEGIES_BY_MARKET
from strategy.params import instantiate
from strategy.persist import persist_drafts

DEFAULT_INTERVAL_S = 30.0

# strategy_configs DB gate + params — re-read every this many seconds so an
# operator (or the reflection/labs apply path) can retire a strategy or change
# its params at runtime without restarting the container.
_CONFIG_TTL_S = 60.0
_configs_cache: dict[tuple[str, str], tuple[int, dict]] | None = None
_configs_ts: float = 0.0


async def _active_configs() -> dict[tuple[str, str], tuple[int, dict]]:
    """(strategy_id, asset_class) → (version, params) for every ACTIVE row.

    Empty dict when the table has NO active rows at all — bootstrap state,
    every registered strategy runs with module defaults at version 1.
    """
    global _configs_cache, _configs_ts
    now = time.monotonic()
    if _configs_cache is not None and now - _configs_ts < _CONFIG_TTL_S:
        return _configs_cache

    async with shared_session_scope() as session:
        rows = (await session.execute(
            select(StrategyConfig.strategy_id, StrategyConfig.asset_class,
                   StrategyConfig.version, StrategyConfig.params)
            .where(StrategyConfig.status == "active")
            .order_by(StrategyConfig.version.desc())
        )).all()

    cfgs: dict[tuple[str, str], tuple[int, dict]] = {}
    for sid, ac, ver, params in rows:
        cfgs.setdefault((sid, ac), (int(ver), dict(params or {})))
    _configs_cache = cfgs
    _configs_ts = now
    return cfgs


def _instantiate_for_market(
    market: MarketAdapter,
    symbols: list[str] | None = None,
    configs: dict[tuple[str, str], tuple[int, dict]] | None = None,
) -> list:
    """Build strategy instances for `market`, bound to their active config.

    `symbols`, when given, is passed to every strategy ctor so the whole
    market shares one dynamically-resolved symbol set (the active universe).
    `configs` (from `_active_configs`) supplies version + tuned params; a
    strategy with no active row is skipped unless `configs` is empty
    (bootstrap mode).

    A KeyError here means a market was registered as a MarketAdapter but has
    no `modules/<market>/__init__.py` exporting STRATEGIES — surface loudly.
    """
    if market.name not in STRATEGIES_BY_MARKET:
        logger.warning(
            f"market {market.name!r} has no registered strategies "
            f"(STRATEGIES_BY_MARKET keys: {sorted(STRATEGIES_BY_MARKET)})"
        )
        return []
    configs = configs or {}
    out = []
    for cls in STRATEGIES_BY_MARKET[market.name]:
        cfg = configs.get((cls.id, market.asset_class))
        if cfg is None:
            if configs:
                logger.debug(f"strategy {cls.id}/{market.asset_class}: no active config row; skip")
                continue
            out.append(instantiate(cls, symbols=symbols))
            continue
        version, params = cfg
        try:
            out.append(instantiate(cls, symbols=symbols, version=version, params=params))
        except Exception as e:
            logger.exception(
                f"strategy {cls.id}/{market.asset_class} v{version}: params rejected "
                f"({e}); falling back to defaults"
            )
            out.append(instantiate(cls, symbols=symbols, version=version))
    return out


async def _tick() -> int:
    configs = await _active_configs()
    # Resolve the crypto active universe once per tick (async — the sync path
    # returns _DEFAULT_UNIVERSE under a running loop), then feed it to every
    # crypto strategy so they analyze the same set ingestion streams + the agent
    # trades. Other markets resolve their own universe internally.
    crypto_symbols = await crypto_universe_async()
    drafts: list[PredictionDraft] = []
    for market in all_markets():
        if not market.is_session_open():
            logger.debug(f"market {market.name} session closed; skip")
            continue
        market_symbols = crypto_symbols if market.name == "crypto" else None
        for strat in _instantiate_for_market(market, market_symbols, configs):
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
