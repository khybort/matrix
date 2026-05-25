"""Synthesis loop — periodic emerging-theme extraction.

Usage:
    uv run python -m synthesis.main                       # hourly default
    uv run python -m synthesis.main --interval 1800       # every 30min
    uv run python -m synthesis.main --once --hours 6
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys

from loguru import logger

from synthesis.synthesize import run_synthesis

DEFAULT_INTERVAL_S = 3600.0   # 1h
DEFAULT_WINDOW_H = 6.0


async def run(interval_s: float, window_hours: float) -> None:
    stop = asyncio.Event()

    def _handle_signal(*_: object) -> None:
        logger.info("shutdown signal received")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    while not stop.is_set():
        try:
            n = await run_synthesis(window_hours)
            if n:
                logger.info(f"tick: {n} themes upserted")
        except Exception as e:
            logger.exception(f"synthesis tick failed: {e}")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except TimeoutError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Matrix synthesis (hourly themes)")
    parser.add_argument(
        "--interval", type=float, default=DEFAULT_INTERVAL_S,
        help=f"Loop interval seconds (default {DEFAULT_INTERVAL_S})",
    )
    parser.add_argument(
        "--hours", type=float, default=DEFAULT_WINDOW_H,
        help=f"Lookback window hours (default {DEFAULT_WINDOW_H})",
    )
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level: <5} | {message}")
    logger.info(
        f"synthesis start: interval={args.interval}s window={args.hours}h once={args.once}"
    )

    if args.once:
        asyncio.run(run_synthesis(args.hours))
    else:
        asyncio.run(run(args.interval, args.hours))


if __name__ == "__main__":
    main()
