"""Discover / refresh the `bist_symbols` table from a live market feed.

Usage:
    uv run matrix-bist-symbols              # discover once
    uv run matrix-bist-symbols --loop 86400 # daily refresh
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from loguru import logger

from ingestion.bist.discover import refresh_universe


async def _run_once(*, bootstrap_active: bool | None) -> int:
    return await refresh_universe(bootstrap_active=bootstrap_active)


async def _run_loop(interval_s: float, *, bootstrap_active: bool | None) -> None:
    while True:
        try:
            await refresh_universe(bootstrap_active=bootstrap_active)
        except Exception as e:
            logger.exception(f"BIST discover failed: {e}")
        await asyncio.sleep(interval_s)


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover BIST symbol universe")
    parser.add_argument("--once", action="store_true", help="Run once and exit (default)")
    parser.add_argument(
        "--loop",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Re-discover on this interval (e.g. 86400 for daily)",
    )
    parser.add_argument(
        "--bootstrap-active",
        action="store_true",
        help="Force active=true on all discovered symbols (first-time bootstrap)",
    )
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level: <5} | {message}")

    bootstrap = True if args.bootstrap_active else None
    if args.loop is not None and not args.once:
        asyncio.run(_run_loop(args.loop, bootstrap_active=bootstrap))
        return

    count = asyncio.run(_run_once(bootstrap_active=bootstrap))
    logger.info(f"bist_symbols refreshed: {count}")


if __name__ == "__main__":
    main()
