"""Reflection loop: scan active strategies, compute window metrics,
propose mutations, write MutationProposal rows.

Proposals are written as `pending`; the labs daemon (`--auto-apply-safe`)
auto-applies lab_promotion / slot_adjustment / rule param_tune for the
SAFE_PARAM_TUNE_STRATEGIES set, everything else waits for the dashboard.
Every proposal is scoped to the StrategyConfig's `asset_class`.

Usage:
    uv run python -m reflection.main                  # default 600s (10min) loop
    uv run python -m reflection.main --interval 60
    uv run python -m reflection.main --once
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from loguru import logger
from matrix_shared import shared_session_scope
from matrix_shared.models import MutationProposal, StrategyConfig
from sqlalchemy import select

from reflection.efficacy import evaluate_applied_proposals, evaluate_challengers, recently_reverted
from reflection.metrics import metrics_window
from reflection.mutate import (
    PARAM_TUNERS,
    _underperforming,
    llm_propose,
    rule_propose,
    rule_propose_param_tune,
)

DEFAULT_INTERVAL_S = 600.0
DEFAULT_WINDOW_HOURS = 24.0

# Minimum hours between consecutive param_tune proposals for the same strategy.
# Prevents the mutation spiral: rapid consecutive tunings on thin samples before
# efficacy has had time to evaluate the previous change.
PARAM_TUNE_COOLDOWN_HOURS = float(os.environ.get("MATRIX_PARAM_TUNE_COOLDOWN_HOURS", "24"))

# How far back to look for recently-tried knobs to pass as skip_knobs.
# A knob tried in this window is skipped in favour of an unexplored one.
PARAM_TUNE_SKIP_KNOBS_DAYS = float(os.environ.get("MATRIX_PARAM_TUNE_SKIP_KNOBS_DAYS", "3"))


async def _mutation_blocked(cfg: StrategyConfig) -> str | None:
    """Why this strategy must not be mutated right now: a `shadow` challenger
    is still being measured, or a proposal from this version is still pending."""
    async with shared_session_scope() as session:
        shadow_v = (await session.execute(
            select(StrategyConfig.version)
            .where(StrategyConfig.strategy_id == cfg.strategy_id)
            .where(StrategyConfig.asset_class == cfg.asset_class)
            .where(StrategyConfig.status == "shadow").limit(1)
        )).scalar_one_or_none()
        if shadow_v is not None:
            return f"challenger v{shadow_v} still running"
        pending = (await session.execute(
            select(MutationProposal.id)
            .where(MutationProposal.strategy_id == cfg.strategy_id)
            .where(MutationProposal.asset_class == cfg.asset_class)
            .where(MutationProposal.from_version == cfg.version)
            .where(MutationProposal.status == "pending").limit(1)
        )).scalar_one_or_none()
        if pending is not None:
            return f"pending proposal #{pending} not applied yet"
    return None


async def _tick(window_hours: float, use_llm: bool, min_outcomes: int, score_trigger: float) -> int:
    """One reflection cycle. Returns number of proposals written."""
    proposals_written = 0
    async with shared_session_scope() as session:
        stmt = select(StrategyConfig).where(StrategyConfig.status == "active")
        active_configs = list((await session.execute(stmt)).scalars())

    for cfg in active_configs:
        try:
            m = await metrics_window(
                cfg.strategy_id, cfg.version,
                asset_class=cfg.asset_class, window_hours=window_hours,
            )
        except Exception as e:
            logger.exception(f"metrics window failed for {cfg.strategy_id}: {e}")
            continue
        logger.info(
            f"{cfg.strategy_id}/{cfg.asset_class} v{cfg.version}: n={m.n_outcomes} episodes ({m.n_raw} rows) "
            f"avg_score={m.avg_score:.4f} win_rate={m.win_rate:.3f} "
            f"pnl={m.total_pnl_usd:.4f}USD"
        )

        draft = None
        # Only strategies that are actually underperforming get mutated — the
        # LLM path used to run for every active config on every tick, so a
        # profitable strategy was re-tuned as eagerly as a losing one and the
        # PnL-aligned rule path was pre-empted whenever the model answered.
        if not _underperforming(m, min_outcomes=min_outcomes, score_trigger=Decimal(str(score_trigger))):
            logger.info(f"{cfg.strategy_id}/{cfg.asset_class}: healthy or thin sample; no mutation")
            continue
        # A running challenger or an unapplied proposal means nothing new can
        # be applied yet — spending an LLM tool-loop on it every tick only
        # produced duplicate pending rows (funding_reversion, 2026-09-13).
        blocked = await _mutation_blocked(cfg)
        if blocked:
            logger.info(f"{cfg.strategy_id}/{cfg.asset_class}: {blocked}; no mutation")
            continue
        if use_llm:
            # Agent tool-loop first: grounds the proposal in recent outcomes,
            # active lessons, and peer-strategy configs. Falls back to the
            # legacy single-shot LLM if subscription unavailable or no parse.
            try:
                from reflection.agent import run_reflection_agent
                draft = await run_reflection_agent(cfg.strategy_id, cfg.params, m)
            except Exception as e:
                logger.warning(f"reflection agent path raised: {e}")
            if draft is None:
                draft = await llm_propose(cfg.strategy_id, cfg.params, m)
        if draft is None:
            draft = rule_propose(
                cfg.params,
                m,
                min_outcomes=min_outcomes,
                score_trigger=Decimal(str(score_trigger)),
            )
        if draft is None:
            # For deterministic strategies (grid/dca/oi_delta), try the
            # param_tune heuristic. matrix_agent uses the weight tuner above.
            # Guard: enforce a cooldown between consecutive param_tune proposals
            # for the same strategy to break the mutation spiral (rapid re-tuning
            # on thin samples before efficacy can judge the previous change).
            skip_knobs: frozenset[str] = frozenset()
            if cfg.strategy_id in PARAM_TUNERS:
                now = datetime.now(UTC)
                cooldown_cutoff = now - timedelta(hours=PARAM_TUNE_COOLDOWN_HOURS)
                async with shared_session_scope() as session:
                    recent_tunes = list((await session.execute(
                        select(MutationProposal)
                        .where(MutationProposal.strategy_id == cfg.strategy_id)
                        .where(MutationProposal.asset_class == cfg.asset_class)
                        .where(MutationProposal.proposal_type == "param_tune")
                        .where(MutationProposal.created_at >= cooldown_cutoff)
                        .order_by(MutationProposal.created_at.desc())
                        .limit(5)
                    )).scalars())
                if recent_tunes:
                    logger.info(
                        f"{cfg.strategy_id}/{cfg.asset_class}: param_tune cooldown "
                        f"({len(recent_tunes)} tune(s) in last "
                        f"{PARAM_TUNE_COOLDOWN_HOURS:.0f}h); skipping"
                    )
                    continue
                # No cooldown active — collect recently-tried knobs so the
                # rotation avoids proposing the same knob that just failed.
                skip_cutoff = now - timedelta(days=PARAM_TUNE_SKIP_KNOBS_DAYS)
                async with shared_session_scope() as session:
                    past_tunes = list((await session.execute(
                        select(MutationProposal.before_params, MutationProposal.after_params)
                        .where(MutationProposal.strategy_id == cfg.strategy_id)
                        .where(MutationProposal.asset_class == cfg.asset_class)
                        .where(MutationProposal.proposal_type == "param_tune")
                        .where(MutationProposal.status.in_(["applied", "reverted"]))
                        .where(MutationProposal.created_at >= skip_cutoff)
                        .order_by(MutationProposal.created_at.desc())
                        .limit(10)
                    )).all())
                tried: set[str] = set()
                for before, after in past_tunes:
                    before = before or {}
                    after = after or {}
                    tried.update(
                        k for k in after
                        if str(after.get(k)) != str(before.get(k))
                    )
                skip_knobs = frozenset(tried)
            draft = rule_propose_param_tune(
                cfg.strategy_id,
                cfg.params,
                m,
                min_outcomes=min_outcomes,
                score_trigger=Decimal(str(score_trigger)),
                skip_knobs=skip_knobs,
            )
        if draft is None:
            logger.info(f"{cfg.strategy_id}: no proposal (criteria not met or no change)")
            continue

        # Oscillation guard: efficacy rolled these exact params back recently —
        # re-proposing them would just re-run the same losing experiment.
        try:
            if await recently_reverted(cfg.strategy_id, cfg.asset_class, draft.after_params):
                logger.info(
                    f"{cfg.strategy_id}/{cfg.asset_class}: draft matches a recently reverted "
                    f"mutation; skipping"
                )
                continue
        except Exception as e:
            logger.warning(f"revert-guard check failed (proceeding): {e}")

        # Avoid spamming duplicate proposals: if a pending exists for the
        # same (asset_class, from_version), skip.
        async with shared_session_scope() as session:
            existing_stmt = select(MutationProposal).where(
                MutationProposal.strategy_id == cfg.strategy_id,
                MutationProposal.asset_class == cfg.asset_class,
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
                "n_raw": m.n_raw,
                "unit": "episode",
                "avg_score": str(m.avg_score),
                "win_rate": str(m.win_rate),
                "total_pnl_usd": str(m.total_pnl_usd),
                "by_symbol": m.by_symbol,
                "by_reason": m.by_reason,
                "window_hours": window_hours,
                "asset_class": cfg.asset_class,
            }
            session.add(
                MutationProposal(
                    strategy_id=cfg.strategy_id,
                    asset_class=cfg.asset_class,
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

    # Keep the per-symbol execution cost current: every "does this edge pay for
    # itself" judgement downstream is denominated in it.
    try:
        from matrix_shared.symbol_costs import refresh as refresh_costs

        await refresh_costs()
    except Exception as e:  # noqa: BLE001 — a refinement, never a blocker
        logger.debug(f"symbol cost refresh skipped: {e}")

    # Auto-grant pass — independent of mutation logic. A strategy version
    # that hits eligibility thresholds gets a paper_trade_certificate so
    # Phase 5 execution can unblock without manual SQL. Failures here MUST
    # NOT crash the tick.
    try:
        from matrix_shared import maybe_grant_certificate
        from matrix_shared.trading_safety import revoke_breached_certificates
        # Revoke first: a granted cert whose version has since breached the
        # drawdown cap must not survive to the next execution tick.
        try:
            for key in await revoke_breached_certificates():
                logger.warning(f"auto-revoked cert (drawdown breach): {key}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"cert revoke pass failed: {e}")
        for cfg in active_configs:
            try:
                ok, _, reason = await maybe_grant_certificate(
                    cfg.strategy_id, cfg.asset_class, cfg.version,
                )
                if ok:
                    logger.info(
                        f"auto-granted cert: {cfg.strategy_id}/{cfg.asset_class}/v{cfg.version}"
                    )
                else:
                    logger.debug(
                        f"cert skip {cfg.strategy_id}/v{cfg.version}: {reason}"
                    )
            except Exception:
                logger.exception(
                    f"auto-grant failed for {cfg.strategy_id} v{cfg.version}"
                )
    except Exception:
        logger.exception("auto-grant pass failed (non-fatal)")

    # Efficacy pass — measure every applied mutation's before/after PnL and
    # auto-rollback the ones that made things significantly worse.
    try:
        await evaluate_applied_proposals()
        await evaluate_challengers()
    except Exception:
        logger.exception("efficacy pass failed (non-fatal)")

    # Slot scoring pass — update per-strategy slot allocations
    try:
        from reflection.slot_scorer import score_strategy_slots
        n_slot_updates = await score_strategy_slots()
        if n_slot_updates:
            logger.info(f"slot scorer: {n_slot_updates} allocations updated")
    except Exception:
        logger.exception("slot scoring pass failed (non-fatal)")

    # Sweep stale pending proposals (7+ days old). They're either superseded
    # by newer drafts or no longer relevant — let the dashboard show only
    # the live queue.
    try:
        from sqlalchemy import text
        async with shared_session_scope() as session:
            result = await session.execute(
                text(
                    "UPDATE mutation_proposals SET status='superseded' "
                    "WHERE status='pending' "
                    "  AND created_at < now() - interval '7 days' "
                    "RETURNING id"
                )
            )
            n_swept = len(list(result.scalars()))
            if n_swept:
                logger.info(f"swept {n_swept} stale pending proposals (>7d)")
    except Exception:
        logger.exception("stale-proposal sweep failed (non-fatal)")

    return proposals_written


async def scan_grants_once() -> int:
    """Run maybe_grant_certificate across every active StrategyConfig once.
    Returns count granted. For the `make cert-scan` operator workflow."""
    from matrix_shared import maybe_grant_certificate
    granted = 0
    async with shared_session_scope() as session:
        stmt = select(StrategyConfig).where(StrategyConfig.status == "active")
        configs = list((await session.execute(stmt)).scalars())
    for cfg in configs:
        try:
            ok, _, reason = await maybe_grant_certificate(
                cfg.strategy_id, cfg.asset_class, cfg.version,
            )
            if ok:
                granted += 1
                logger.info(
                    f"granted cert: {cfg.strategy_id}/{cfg.asset_class}/v{cfg.version}"
                )
            else:
                logger.info(
                    f"skip {cfg.strategy_id}/{cfg.asset_class}/v{cfg.version}: {reason}"
                )
        except Exception:
            logger.exception(f"grant failed for {cfg.strategy_id} v{cfg.version}")
    return granted


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
    parser.add_argument(
        "--scan-grants", action="store_true",
        help="Scan active strategies; auto-grant paper_trade_certificate if eligible. Exits.",
    )
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level: <5} | {message}")
    logger.info(
        f"reflection start: interval={args.interval}s window={args.window_hours}h "
        f"llm={'disabled' if args.no_llm else 'auto'} once={args.once}"
    )

    use_llm = not args.no_llm
    if args.scan_grants:
        n = asyncio.run(scan_grants_once())
        logger.info(f"scan-grants: granted {n} cert(s)")
    elif args.once:
        asyncio.run(_tick(args.window_hours, use_llm, args.min_outcomes, args.score_trigger))
    else:
        asyncio.run(
            run(args.interval, args.window_hours, use_llm, args.min_outcomes, args.score_trigger)
        )


if __name__ == "__main__":
    main()
