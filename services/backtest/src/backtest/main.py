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
import time

from loguru import logger

from matrix_shared import shared_session_scope
from matrix_shared.models import StrategyConfig
from matrix_shared.trading_safety import maybe_grant_certificate
from sqlalchemy import select

from backtest.paper_trade import (
    close_due_positions,
    expire_stale_predictions,
    open_due_positions,
    snapshot_wallet,
)

DEFAULT_INTERVAL_S = 10.0
# Certificate check is expensive (full outcome scan per strategy). Run
# infrequently — once per hour is plenty; eligibility changes on a days
# timescale (60-day observation gate).
CERT_CHECK_INTERVAL_S = 3600.0


async def _check_certificates() -> None:
    """Auto-grant paper_trade_certificates for eligible active strategies.

    Calls maybe_grant_certificate for every (strategy_id, asset_class, version)
    tuple that currently has an active strategy_configs row. Idempotent: already-
    granted certs are skipped. Failures are logged but never raise — this must
    not interrupt the trading loop.
    """
    async with shared_session_scope() as session:
        rows = (await session.execute(
            select(
                StrategyConfig.strategy_id,
                StrategyConfig.asset_class,
                StrategyConfig.version,
            ).where(StrategyConfig.status == "active")
        )).all()

    for r in rows:
        try:
            granted, verdict, reason = await maybe_grant_certificate(
                r.strategy_id, r.asset_class, r.version,
                granted_by="auto-backtest-loop",
            )
            if granted:
                logger.info(
                    f"cert GRANTED: {r.strategy_id}/{r.asset_class}/v{r.version} "
                    f"n={verdict.metrics.get('n_outcomes')} "
                    f"days={verdict.metrics.get('observation_days')} "
                    f"win={verdict.metrics.get('win_rate')}"
                )
            elif verdict and not verdict.eligible:
                logger.debug(
                    f"cert not yet eligible: {r.strategy_id}/v{r.version} — "
                    + "; ".join(verdict.reasons)
                )
        except Exception as e:
            logger.warning(f"cert check failed for {r.strategy_id}: {e}")


async def _tick() -> tuple[int, int, int]:
    # Order matters: expire stale predictions first so they're not seen as
    # candidates by open_due_positions. Then close due open positions
    # (frees slots), then fill freed slots with fresh candidates.
    expired = await expire_stale_predictions()
    closed = await close_due_positions()
    opened = await open_due_positions()
    await snapshot_wallet()
    return opened, closed, expired


async def run(interval_s: float) -> None:
    stop = asyncio.Event()

    def _handle_signal(*_: object) -> None:
        logger.info("shutdown signal received")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    last_cert_check = 0.0
    while not stop.is_set():
        try:
            opened, closed, expired = await _tick()
            if opened or closed or expired:
                logger.info(f"tick: opened={opened} closed={closed} expired={expired}")
        except Exception as e:
            logger.exception(f"tick error: {e}")

        now = time.monotonic()
        if now - last_cert_check >= CERT_CHECK_INTERVAL_S:
            await _check_certificates()
            last_cert_check = now

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
