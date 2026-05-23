"""Labs main loop.

Two concurrent rhythms:
    - eval_interval: emit signals from every active experiment, then score
      any due evaluations
    - evolution_interval: every N seconds, run a selection + reproduction
      cycle if enough evidence has accumulated

Usage:
    uv run python -m labs.main                       # default ~ 20s eval, 5min evolve
    uv run python -m labs.main --eval-interval 15 --evolve-interval 180
    uv run python -m labs.main --once                # one eval + score + (try) evolve
    uv run python -m labs.main --seed-only           # seed population and exit
    uv run python -m labs.main --leaderboard         # print top-N and exit
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from decimal import Decimal

from loguru import logger
from sqlalchemy import desc, select

from matrix_shared import session_scope
from matrix_shared.models import LabEvaluation, LabExperiment

from labs.evaluate import emit_signals, score_due_evaluations
from labs.evolve import seed_initial_population, run_evolution_cycle

DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT")
DEFAULT_EVAL_INTERVAL_S = 20.0
DEFAULT_EVOLVE_INTERVAL_S = 300.0


async def _eval_tick(symbols: list[str]) -> tuple[int, int, int]:
    opened = await emit_signals(symbols)
    scored, stale = await score_due_evaluations()
    return opened, scored, stale


async def _leaderboard(limit: int = 15) -> None:
    async with session_scope() as session:
        stmt = (
            select(LabExperiment)
            .where(LabExperiment.status == "active")
            .order_by(desc(LabExperiment.fitness_score))
            .limit(limit)
        )
        rows = list((await session.execute(stmt)).scalars())

    if not rows:
        print("no active experiments")
        return

    print(f"{'id':>6} {'gen':>4} {'n_eval':>6} {'n_sig':>6} {'wins':>5} "
          f"{'fitness':>9} {'thr':>6} {'hor':>4}")
    for e in rows:
        params = e.params or {}
        thr = params.get("signal_threshold", "")
        hor = params.get("horizon_seconds", "")
        print(
            f"{str(e.id)[:6]:>6} {e.generation:>4} {e.n_evaluations:>6} "
            f"{e.n_signals:>6} {e.n_wins:>5} "
            f"{Decimal(e.fitness_score):>9.4f} {str(thr)[:6]:>6} {str(hor):>4}"
        )

    # Show top 1's weights
    top = rows[0]
    print(f"\nTop genome weights ({top.id}):")
    for k, v in (top.params.get("weights") or {}).items():
        print(f"  {k:>14} = {v}")


async def run(
    symbols: list[str],
    eval_interval_s: float,
    evolve_interval_s: float,
    min_evals: int = 5,
) -> None:
    await seed_initial_population()

    stop = asyncio.Event()

    def _handle_signal(*_: object) -> None:
        logger.info("shutdown signal received")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    last_evolve = 0.0
    while not stop.is_set():
        loop_started = asyncio.get_event_loop().time()
        try:
            opened, scored, stale = await _eval_tick(symbols)
            if opened or scored or stale:
                logger.info(
                    f"eval: opened={opened} scored={scored} stale={stale}"
                )
        except Exception as e:
            logger.exception(f"eval tick failed: {e}")

        if loop_started - last_evolve >= evolve_interval_s:
            try:
                report = await run_evolution_cycle(min_eval_per_gen=min_evals)
                if report.born or report.retired:
                    logger.info(
                        f"evolution: gen={report.new_generation} "
                        f"elites={report.elites} born={report.born} "
                        f"retired={report.retired}"
                    )
            except Exception as e:
                logger.exception(f"evolution failed: {e}")
            last_evolve = loop_started

        try:
            await asyncio.wait_for(stop.wait(), timeout=eval_interval_s)
        except TimeoutError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Matrix labs (evolutionary algorithm search)")
    parser.add_argument("--symbols", nargs="*", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--eval-interval", type=float, default=DEFAULT_EVAL_INTERVAL_S)
    parser.add_argument("--evolve-interval", type=float, default=DEFAULT_EVOLVE_INTERVAL_S)
    parser.add_argument("--once", action="store_true", help="Run one eval tick + score + try evolve")
    parser.add_argument("--seed-only", action="store_true", help="Seed initial population and exit")
    parser.add_argument("--leaderboard", action="store_true", help="Print top genomes and exit")
    parser.add_argument(
        "--min-evals", type=int, default=5,
        help="Min evaluations per genome to be eligible in evolution (default 5)",
    )
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level: <5} | {message}")

    if args.leaderboard:
        asyncio.run(_leaderboard())
        return

    if args.seed_only:
        asyncio.run(seed_initial_population())
        return

    if args.once:
        async def _one():
            await seed_initial_population()
            opened, scored, stale = await _eval_tick(args.symbols)
            logger.info(f"once: opened={opened} scored={scored} stale={stale}")
            report = await run_evolution_cycle(min_eval_per_gen=args.min_evals)
            logger.info(
                f"evolve once: gen={report.new_generation} elites={report.elites} "
                f"born={report.born} retired={report.retired}"
            )
        asyncio.run(_one())
        return

    logger.info(
        f"labs start: symbols={args.symbols} eval={args.eval_interval}s "
        f"evolve={args.evolve_interval}s min_evals={args.min_evals}"
    )
    asyncio.run(
        run(args.symbols, args.eval_interval, args.evolve_interval, args.min_evals)
    )


if __name__ == "__main__":
    main()
