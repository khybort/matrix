"""Hourly slot scorer: adjusts per-strategy allocated_slots based on performance.

Score formula: 0.4 * win_rate + 0.3 * clamp(avg_pnl_pct / 0.02, -1, 1) + 0.3 * clamp(total_pnl_usd / 10, -1, 1)
Consecutive losses >= 5: auto-cut to 1 slot (no approval needed).
Changes > 50% reduction or recovery from 1: recorded as an applied slot_adjustment
MutationProposal (audit trail — live slots already updated in the same tick).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm.exc import StaleDataError

from matrix_shared import shared_session_scope
from matrix_shared.stats import wilson_lower
from matrix_shared.models import MutationProposal, PaperPosition, Prediction, StrategyConfig, Wallet
from matrix_shared.models.slot_config import StrategySlotConfig

# Was 5/30; raised CONSEC threshold to 8 (more patient — a short losing streak
# isn't enough to cut a strategy that's recovering). Lookback dropped to 10 so
# fresh evidence (post-TP/SL fix) reweights faster — the slot scorer can spot
# improvement within hours instead of waiting for 30 trades to roll over.
CONSECUTIVE_LOSS_AUTO_CUT = 8
# 2026-09-13 (docs/AUTONOMY_PLAN.md P1.5): a 10-trade win rate has ±16pp standard
# error — the 0.30/0.50/0.70 tiers were pure noise. Score over the last 30 and
# refuse to move slots on fewer than MIN_N_FOR_SLOT_CHANGE closed positions
# (the consecutive-loss auto-cut still fires on its own evidence).
LAST_N_POSITIONS = 30
SHADOW_WALLET_NAME = "shadow"
MIN_N_FOR_SLOT_CHANGE = int(os.environ.get("MATRIX_SLOT_MIN_N", "30"))


def _perf_score(win_rate: float, avg_pnl_pct: float, total_pnl_usd: float) -> float:
    pct_clamped = max(-1.0, min(1.0, avg_pnl_pct / 0.02))
    # $10 over 30 trades is a healthy positive bias; $-10 floors the term.
    pnl_clamped = max(-1.0, min(1.0, total_pnl_usd / 10.0))
    return 0.4 * win_rate + 0.3 * pct_clamped + 0.3 * pnl_clamped


def _auto_cut_slots(old_slots: int) -> int:
    """A losing streak caps a strategy at one slot; it never grants one.

    This used to be `= 1`, which re-armed every strategy another rule had
    pulled to zero the moment its streak reached eight. On 2026-10-09 the only
    two strategies holding champion slots in crypto were inverse_carry (30
    straight losses) and xexch_funding_arb (23): the streak itself kept them
    in the book while every strategy with a mixed record sat at zero.
    """
    return min(old_slots, 1)


def _slots_for_score(score: float, base_share: int) -> int:
    if score >= 0.70:
        return max(1, base_share * 2)
    elif score >= 0.50:
        return max(1, base_share)
    elif score >= 0.30:
        return max(1, base_share // 2)
    else:
        return max(1, base_share // 4)


async def _entry_edge_verdict(strategy_id: str, asset_class: str) -> str:
    """`pays` | `harmful` | `unproven` | `unknown` for this strategy's entry
    timing, from the controlled study in `matrix_shared.edge_study`.

    `unknown` means the study has no answer yet — a cold cache after a restart,
    or a strategy it has not reached. That is NOT `unproven`, and the
    difference cost real capital: on 2026-09-20 a reload emptied the cache and
    this pass took momentum_xs from 7 slots to 3 seconds after the previous
    pass had promoted it on a confirmed +29.7 bps edge. An allocation granted
    on evidence is never reduced because the evidence is temporarily
    unreadable.

    Realised PnL and entry quality are different questions, and the slot pass
    needs both answers. `pays` protects a strategy from the realised-loss
    demotion; `harmful` demotes one that the realised rule would have kept.

    On 2026-09-19 `oi_delta` was demoted to zero slots on realised loss while
    the study put its entries +19 bps above random entries on the same symbols,
    sides and brackets (t=2.90, n=274) — its loss was cost and sizing, not
    signal. Throwing that away is how a system with one working idea ends up
    with none.
    """
    try:
        from matrix_shared.edge_study import strategy_edge, verdict
        from matrix_shared.trading import execution_cost_bps

        cost = float(execution_cost_bps(asset_class)) * 2
        row = await strategy_edge(strategy_id, asset_class)
        if row is None:
            return "unknown"
        v = verdict(row, cost_bps=cost)
    except Exception as e:  # noqa: BLE001 — advisory; never block the slot pass
        logger.debug(f"edge guard unavailable for {strategy_id}/{asset_class} ({e})")
        return "unknown"
    if v == "pays":
        logger.warning(
            f"slot demote SKIPPED for {strategy_id}/{asset_class}: entry edge "
            f"{row['edge_bps']:+.1f} bps vs random (t={row['t']:.2f}, n={row['n']}) "
            f"clears the {cost:.0f} bps round trip — losing on cost/sizing, not signal"
        )
    return v
    if v == "pays":
        logger.warning(
            f"slot demote SKIPPED for {strategy_id}/{asset_class}: entry edge "
            f"{row['edge_bps']:+.1f} bps vs random (t={row['t']:.2f}, n={row['n']}) "
            f"clears the {cost:.0f} bps round trip — losing on cost/sizing, not signal"
        )
        return True
    return False


async def _flush_config(session, config: StrategySlotConfig) -> bool:
    """Flush one config's changes inside a savepoint. A slot row deleted by
    another process mid-pass (labs/test cleanup, operator SQL) used to raise
    StaleDataError at commit and throw away the whole hourly pass."""
    try:
        async with session.begin_nested():
            await session.flush()
        return True
    except StaleDataError:
        logger.info(f"slot row vanished mid-pass: {config.strategy_id}/{config.asset_class}; skipping")
        session.expunge(config)
        return False


async def score_strategy_slots() -> int:
    """Score all StrategySlotConfigs and update allocated_slots.

    Returns the count of configs that had their allocated_slots changed.
    """
    updated = 0
    async with shared_session_scope() as session:
        configs = list((await session.execute(select(StrategySlotConfig))).scalars())
        if not configs:
            return 0

        wallet_ids = {c.wallet_id for c in configs}
        wallets: dict = {
            w.id: w
            for w in (
                await session.execute(select(Wallet).where(Wallet.id.in_(wallet_ids)))
            ).scalars()
        }

        for config in configs:
            wallet = wallets.get(config.wallet_id)
            if wallet is None:
                continue
            # Challenger wallets don't own slots: the paper engine runs the
            # shadow pass under the CHAMPION wallet's slot configs, so scoring
            # the shadow wallet's rows only produced noise proposals.
            if wallet.name == SHADOW_WALLET_NAME:
                continue

            # Share the wallet only among strategies that actually run here:
            # slot rows for retired/phantom strategies (BIST modules on the
            # crypto wallet, test residue) had inflated the divisor to 22 and
            # cut every real strategy's base share to 80//22 = 3 instead of 8.
            n_active = (
                await session.execute(
                    select(func.count(func.distinct(StrategySlotConfig.strategy_id)))
                    .select_from(StrategySlotConfig)
                    .join(StrategyConfig, (StrategyConfig.strategy_id == StrategySlotConfig.strategy_id)
                          & (StrategyConfig.asset_class == StrategySlotConfig.asset_class))
                    .where(StrategySlotConfig.wallet_id == config.wallet_id)
                    .where(StrategyConfig.status.in_(("active", "shadow")))
                )
            ).scalar_one() or 1
            base_share = max(1, wallet.max_concurrent_positions // n_active)

            # Last N closed positions for this strategy+wallet
            rows = list(
                (
                    await session.execute(
                        select(PaperPosition)
                        .join(Prediction, Prediction.id == PaperPosition.prediction_id)
                        .where(PaperPosition.wallet_id == config.wallet_id)
                        .where(PaperPosition.status == "closed")
                        .where(Prediction.strategy_id == config.strategy_id)
                        .order_by(PaperPosition.closed_at.desc())
                        .limit(LAST_N_POSITIONS)
                    )
                ).scalars()
            )

            if not rows:
                continue

            wins = sum(1 for r in rows if (r.pnl_usd or 0) > 0)
            # Conservative win rate: Wilson lower bound, so thin samples score
            # low instead of lucky.
            win_rate = wilson_lower(wins, len(rows))
            avg_pnl_pct = (
                sum(
                    float(r.pnl_usd or 0) / float(r.notional_usd or 1)
                    for r in rows
                )
                / len(rows)
            )
            total_pnl_usd = sum(float(r.pnl_usd or 0) for r in rows)

            # Consecutive losses: walk from newest closed backward
            sorted_rows = sorted(
                rows,
                key=lambda r: r.closed_at or datetime.min.replace(tzinfo=UTC),
                reverse=True,
            )
            consec = 0
            for r in sorted_rows:
                if (r.pnl_usd or 0) < 0:
                    consec += 1
                else:
                    break

            score = _perf_score(win_rate, avg_pnl_pct, total_pnl_usd)
            old_slots = config.allocated_slots
            # Asked once per config rather than at each branch that wants it:
            # the answers could otherwise disagree within one pass if a
            # background refresh of the study landed between two of them.
            edge_v = await _entry_edge_verdict(config.strategy_id, config.asset_class)

            if consec >= CONSECUTIVE_LOSS_AUTO_CUT:
                new_slots = _auto_cut_slots(old_slots)
                if config.consecutive_losses < CONSECUTIVE_LOSS_AUTO_CUT:
                    logger.warning(
                        f"slot auto-cut: {config.strategy_id}/{config.asset_class} "
                        f"consecutive_losses={consec} → slots {old_slots}→{new_slots}"
                    )
            elif len(rows) < MIN_N_FOR_SLOT_CHANGE:
                # Not enough evidence to move capital either way.
                config.perf_score = score
                config.last_evaluated_at = datetime.now(UTC)
                await _flush_config(session, config)
                continue
            else:
                new_slots = _slots_for_score(score, base_share)
                if edge_v == "unknown" and old_slots > new_slots:
                    # Hold what evidence already bought. On 2026-09-20 a reload
                    # emptied the edge cache and this pass took momentum_xs from
                    # 7 slots to 3, seconds after the previous pass had promoted
                    # it on a confirmed +29.7 bps edge. `perf_score` alone would
                    # claw back an allocation while the study is merely silent.
                    logger.info(
                        f"slot hold: {config.strategy_id}/{config.asset_class} keeps "
                        f"{old_slots} (score would say {new_slots}) — edge study has no "
                        "answer yet, which is not the same as no edge"
                    )
                    new_slots = old_slots
                elif edge_v == "pays":
                    # Allocate on evidence, not on trailing PnL. `perf_score`
                    # is a rearview mirror: momentum_xs sat at one slot — the
                    # floor — while the controlled study put its entries +31
                    # bps over random timing and +33 over random direction,
                    # because its realised history was made of fills that
                    # arrived halfway through their own horizon. A strategy
                    # that beats a null by more than the round trip gets at
                    # least a full share of the wallet.
                    if new_slots < base_share:
                        logger.warning(
                            f"slot promote: {config.strategy_id}/{config.asset_class} "
                            f"{new_slots}→{base_share} on measured entry edge"
                        )
                    new_slots = max(new_slots, base_share)

            config.perf_score = score
            config.consecutive_losses = consec
            config.last_evaluated_at = datetime.now(UTC)
            config.updated_at = datetime.now(UTC)

            if new_slots == old_slots:
                await _flush_config(session, config)
                continue

            config.allocated_slots = new_slots
            updated += 1

            needs_approval = (old_slots > 0 and new_slots < old_slots * 0.5) or (
                old_slots == 1 and new_slots > 1
            )

            if needs_approval:
                session.add(
                    MutationProposal(
                        strategy_id=config.strategy_id,
                        asset_class=config.asset_class,
                        from_version=0,
                        to_version=0,
                        proposal_type="slot_adjustment",
                        before_params={"allocated_slots": old_slots},
                        after_params={"allocated_slots": new_slots},
                        metrics_window={
                            "win_rate": str(round(win_rate, 4)),
                            "avg_pnl_pct": str(round(avg_pnl_pct, 6)),
                            "total_pnl_usd": str(round(total_pnl_usd, 4)),
                            "perf_score": str(round(score, 4)),
                            "consecutive_losses": consec,
                            "n_evaluated": len(rows),
                        },
                        rationale=(
                            f"Slot adjustment: {config.strategy_id}/{config.asset_class} "
                            f"{old_slots}→{new_slots}. "
                            f"perf_score={score:.4f} win_rate={win_rate:.3f} "
                            f"avg_pnl_pct={avg_pnl_pct:.4f} total_pnl_usd={total_pnl_usd:.2f} "
                            f"consec_losses={consec}"
                        ),
                        status="applied",
                        source="slot_scorer",
                        applied_at=datetime.now(UTC),
                    )
                )
                logger.info(
                    f"slot audit: {config.strategy_id}/{config.asset_class} "
                    f"{old_slots}→{new_slots} (recorded as applied)"
                )
            else:
                logger.info(
                    f"slot updated: {config.strategy_id}/{config.asset_class} "
                    f"{old_slots}→{new_slots} score={score:.4f}"
                )
            if not await _flush_config(session, config):
                updated -= 1

    return updated
