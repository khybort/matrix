"""Paper-trade engine loop.

Every TICK_S seconds:
    1. Open positions for new predictions
    2. Close positions whose horizon has passed; write Outcome

Usage:
    uv run python -m backtest.main                # default 10s loop
    uv run python -m backtest.main --interval 5
    uv run python -m backtest.main --once
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys

from loguru import logger

from backtest.paper_trade import close_due_positions, open_due_positions

DEFAULT_INTERVAL_S = 10.0


async def _tick() -> tuple[int, int]:
    opened = await open_due_positions()
    closed = await close_due_positions()
    return opened, closed


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
            opened, closed = await _tick()
            if opened or closed:
                logger.info(f"tick: opened={opened} closed={closed}")
        except Exception as e:
            logger.exception(f"tick error: {e}")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except TimeoutError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Matrix paper-trade engine")
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL_S,
        help=f"Engine loop interval seconds (default {DEFAULT_INTERVAL_S})",
    )
    parser.add_argument("--once", action="store_true", help="Run one tick and exit")
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level: <5} | {message}")
    logger.info(f"backtest start: interval={args.interval}s once={args.once}")

    if args.once:
        asyncio.run(_tick())
    else:
        asyncio.run(run(args.interval))


if __name__ == "__main__":
    main()
