"""Reflection loop: scan active strategies, compute window metrics,
propose mutations, write MutationProposal rows.

Mutations are not auto-applied — they're stored as `pending` proposals
for downstream review (manual or future auto-promotion logic).

Usage:
    uv run python -m reflection.main                  # default 600s (10min) loop
    uv run python -m reflection.main --interval 60
    uv run python -m reflection.main --once
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from decimal import Decimal

from loguru import logger  # noqa: I001
from sqlalchemy import select

from matrix_shared import session_scope
from matrix_shared.models import MutationProposal, StrategyConfig

from reflection.metrics import metrics_window
from reflection.mutate import llm_propose, rule_propose

DEFAULT_INTERVAL_S = 600.0
DEFAULT_WINDOW_HOURS = 24.0


async def _tick(window_hours: float, use_llm: bool, min_outcomes: int, score_trigger: float) -> int:
    """One reflection cycle. Returns number of proposals written."""
    proposals_written = 0
    async with session_scope() as session:
        stmt = select(StrategyConfig).where(StrategyConfig.status == "active")
        active_configs = list((await session.execute(stmt)).scalars())

    for cfg in active_configs:
        try:
            m = await metrics_window(cfg.strategy_id, cfg.version, window_hours=window_hours)
        except Exception as e:
            logger.exception(f"metrics window failed for {cfg.strategy_id}: {e}")
            continue
        logger.info(
            f"{cfg.strategy_id} v{cfg.version}: n={m.n_outcomes} "
            f"avg_score={m.avg_score:.4f} win_rate={m.win_rate:.3f} "
            f"pnl={m.total_pnl_usd:.4f}USD"
        )

        draft = None
        if use_llm:
            draft = await llm_propose(cfg.strategy_id, cfg.params, m)
        if draft is None:
            draft = rule_propose(
                cfg.params,
                m,
                min_outcomes=min_outcomes,
                score_trigger=Decimal(str(score_trigger)),
            )
        if draft is None:
            logger.info(f"{cfg.strategy_id}: no proposal (criteria not met or no change)")
            continue

        # Avoid spamming duplicate proposals: if a pending exists for the
        # same from_version, skip.
        async with session_scope() as session:
            existing_stmt = select(MutationProposal).where(
                MutationProposal.strategy_id == cfg.strategy_id,
                MutationProposal.from_version == cfg.version,
                MutationProposal.status == "pending",
            )
            existing = (await session.execute(existing_stmt)).scalars().first()
            if existing is not None:
                logger.info(
                    f"{cfg.strategy_id}: pending proposal already exists (#{existing.id})"
                )
                continue

            metrics_snapshot = {
                "n_outcomes": m.n_outcomes,
                "avg_score": str(m.avg_score),
                "win_rate": str(m.win_rate),
                "total_pnl_usd": str(m.total_pnl_usd),
                "by_symbol": m.by_symbol,
                "window_hours": window_hours,
            }
            session.add(
                MutationProposal(
                    strategy_id=cfg.strategy_id,
                    from_version=cfg.version,
                    to_version=cfg.version + 1,
                    proposal_type=draft.proposal_type,
                    before_params=draft.before_params,
                    after_params=draft.after_params,
                    metrics_window=metrics_snapshot,
                    rationale=draft.rationale,
                    status="pending",
                    source=draft.source,
                )
            )
        proposals_written += 1
        logger.info(
            f"{cfg.strategy_id} v{cfg.version} → proposal "
            f"({draft.proposal_type}, source={draft.source})"
        )

    return proposals_written


async def run(
    interval_s: float,
    window_hours: float,
    use_llm: bool,
    min_outcomes: int,
    score_trigger: float,
) -> None:
    stop = asyncio.Event()

    def _handle_signal(*_: object) -> None:
        logger.info("shutdown signal received")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    while not stop.is_set():
        try:
            n = await _tick(window_hours, use_llm, min_outcomes, score_trigger)
            logger.info(f"tick: {n} new proposals")
        except Exception as e:
            logger.exception(f"reflection tick failed: {e}")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except TimeoutError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Matrix reflection (self-improvement)")
    parser.add_argument(
        "--interval", type=float, default=DEFAULT_INTERVAL_S,
        help=f"Loop interval seconds (default {DEFAULT_INTERVAL_S})",
    )
    parser.add_argument(
        "--window-hours", type=float, default=DEFAULT_WINDOW_HOURS,
        help=f"Outcome lookback window (default {DEFAULT_WINDOW_HOURS}h)",
    )
    parser.add_argument(
        "--no-llm", action="store_true",
        help="Force rule-based even if AI_GATEWAY_API_KEY is set",
    )
    parser.add_argument(
        "--min-outcomes", type=int, default=10,
        help="Min n_outcomes required to consider mutation (default 10)",
    )
    parser.add_argument(
        "--score-trigger", type=float, default=-0.05,
        help="Trigger mutation when avg_score below this (default -0.05)",
    )
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level: <5} | {message}")
    logger.info(
        f"reflection start: interval={args.interval}s window={args.window_hours}h "
        f"llm={'disabled' if args.no_llm else 'auto'} once={args.once}"
    )

    use_llm = not args.no_llm
    if args.once:
        asyncio.run(_tick(args.window_hours, use_llm, args.min_outcomes, args.score_trigger))
    else:
        asyncio.run(
            run(args.interval, args.window_hours, use_llm, args.min_outcomes, args.score_trigger)
        )


if __name__ == "__main__":
    main()
