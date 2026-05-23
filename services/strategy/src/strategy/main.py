"""Strategy generator loop.

Each tick: every registered strategy module runs `generate()`, drafts go to
predictions table. Paper-trade engine (services/backtest) consumes from there.

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

from loguru import logger

from strategy.base import PredictionDraft
from strategy.modules.trade_flow_imbalance import TradeFlowImbalance
from strategy.persist import persist_drafts

DEFAULT_INTERVAL_S = 30.0


def _registered_strategies() -> list:
    return [TradeFlowImbalance()]


async def _tick() -> int:
    drafts: list[PredictionDraft] = []
    for strat in _registered_strategies():
        try:
            ds = await strat.generate()
            drafts.extend(ds)
        except Exception as e:
            logger.exception(f"strategy {strat.id} failed: {e}")
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
    logger.info(f"strategy start: interval={args.interval}s once={args.once}")

    if args.once:
        asyncio.run(_tick())
    else:
        asyncio.run(run(args.interval))


if __name__ == "__main__":
    main()
