"""Hourly slot scorer: adjusts per-strategy allocated_slots based on performance.

Score formula: 0.4 * win_rate + 0.3 * clamp(avg_pnl_pct / 0.02, -1, 1) + 0.3 * clamp(total_pnl_usd / 10, -1, 1)
Consecutive losses >= 5: auto-cut to 1 slot (no approval needed).
Changes > 50% reduction or recovery from 1: written as slot_adjustment
MutationProposal for operator approval via the lessons dashboard.
"""

from __future__ import annotations

from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import func, select

from matrix_shared import shared_session_scope
from matrix_shared.models import MutationProposal, PaperPosition, Prediction, Wallet
from matrix_shared.models.slot_config import StrategySlotConfig

CONSECUTIVE_LOSS_AUTO_CUT = 5
LAST_N_POSITIONS = 30


def _perf_score(win_rate: float, avg_pnl_pct: float, total_pnl_usd: float) -> float:
    pct_clamped = max(-1.0, min(1.0, avg_pnl_pct / 0.02))
    # $10 over 30 trades is a healthy positive bias; $-10 floors the term.
    pnl_clamped = max(-1.0, min(1.0, total_pnl_usd / 10.0))
    return 0.4 * win_rate + 0.3 * pct_clamped + 0.3 * pnl_clamped


def _slots_for_score(score: float, base_share: int) -> int:
    if score >= 0.70:
        return max(1, base_share * 2)
    elif score >= 0.50:
        return max(1, base_share)
    elif score >= 0.30:
        return max(1, base_share // 2)
    else:
        return max(1, base_share // 4)


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

            n_active = (
                await session.execute(
                    select(func.count(StrategySlotConfig.strategy_id)).where(
                        StrategySlotConfig.wallet_id == config.wallet_id
                    )
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
            win_rate = wins / len(rows)
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

            if consec >= CONSECUTIVE_LOSS_AUTO_CUT:
                new_slots = 1
                if config.consecutive_losses < CONSECUTIVE_LOSS_AUTO_CUT:
                    logger.warning(
                        f"slot auto-cut: {config.strategy_id}/{config.asset_class} "
                        f"consecutive_losses={consec} → slots {old_slots}→1"
                    )
            else:
                new_slots = _slots_for_score(score, base_share)

            config.perf_score = score
            config.consecutive_losses = consec
            config.last_evaluated_at = datetime.now(UTC)
            config.updated_at = datetime.now(UTC)

            if new_slots == old_slots:
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
                        status="pending",
                        source="slot_scorer",
                    )
                )
                logger.info(
                    f"slot proposal: {config.strategy_id}/{config.asset_class} "
                    f"{old_slots}→{new_slots} (awaiting approval)"
                )
            else:
                logger.info(
                    f"slot updated: {config.strategy_id}/{config.asset_class} "
                    f"{old_slots}→{new_slots} score={score:.4f}"
                )

    return updated
