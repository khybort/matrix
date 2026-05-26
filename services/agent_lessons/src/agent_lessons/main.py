"""agent_lessons daemon: continuously synthesize lessons from decisions.

Loop cadence default is hourly — the user said "öğrenmeli HEP," and
hourly is "always" for human pattern-recognition while keeping the
write rate sane (each pass writes ≤ one lesson per bucket). Per-tick
synthesis would write churny rows without new information.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys

from loguru import logger

from agent_lessons.synthesizer import (
    DEFAULT_STRATEGY_ID,
    LOOKBACK_HOURS,
    synthesize,
)

DEFAULT_INTERVAL_S = 3600.0  # hourly


async def run(strategy_id: str, lookback_hours: int, interval_s: float) -> None:
    stop = asyncio.Event()

    def _handle_signal(*_: object) -> None:
        logger.info("shutdown signal received")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    while not stop.is_set():
        try:
            n = await synthesize(strategy_id, lookback_hours=lookback_hours)
            logger.info(f"tick: {n} new/updated lesson(s)")
        except Exception as e:
            logger.exception(f"synthesize failed (non-fatal): {e}")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except TimeoutError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Matrix agent_lessons synthesizer")
    parser.add_argument("--strategy-id", default=DEFAULT_STRATEGY_ID)
    parser.add_argument("--lookback-hours", type=int, default=LOOKBACK_HOURS)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_S,
                        help=f"Loop interval seconds (default {int(DEFAULT_INTERVAL_S)} = hourly)")
    parser.add_argument("--once", action="store_true",
                        help="Run one synthesis pass and exit")
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level: <5} | {message}")
    logger.info(
        f"agent_lessons start: strategy={args.strategy_id} "
        f"lookback={args.lookback_hours}h interval={args.interval}s once={args.once}"
    )

    if args.once:
        asyncio.run(synthesize(args.strategy_id, lookback_hours=args.lookback_hours))
    else:
        asyncio.run(run(args.strategy_id, args.lookback_hours, args.interval))


if __name__ == "__main__":
    main()
