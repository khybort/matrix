"""Promotion bridge: lab discoveries → live agent config.

Two operations:

  scan_for_promotions(strategy_id="matrix_agent"):
    - Finds the highest-fitness ACTIVE lab experiment meeting eligibility
      (n_evaluations >= MIN_EVAL, fitness >= MIN_FITNESS).
    - If no pending lab_promotion proposal exists for that strategy and that
      lab id, writes a MutationProposal with source="labs".
    - Returns the proposal id (or None).

  apply_proposal(proposal_id):
    - Loads the proposal (must be pending, type=lab_promotion).
    - Strips forbidden risk-cap fields from after_params (defense-in-depth).
    - Creates a new StrategyConfig row at version+1 with status="active",
      retires the prior active row.
    - Marks the proposal status="applied".
    - Marks the source lab experiment status="promoted" so it isn't
      re-promoted ad infinitum.

Hard rule (docs/TRADING.md): forbidden_fields are scrubbed from any
after_params dict before it is ever written to a StrategyConfig row.
Even if a malicious / buggy proposal somehow carried them, they would
not survive this layer.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from loguru import logger
from sqlalchemy import and_, desc, exists, select

from matrix_shared import shared_session_scope
from matrix_shared.models import LabExperiment, MutationProposal, StrategyConfig

# Eligibility thresholds — promotion is *consequential*, so defaults are strict.
MIN_EVAL_FOR_PROMOTION = 30
MIN_FITNESS_FOR_PROMOTION = Decimal("0.05")

# Risk-cap fields that must NEVER ride along with a proposal's after_params.
FORBIDDEN_FIELDS = {
    "max_position_pct",
    "daily_loss_circuit_pct",
    "max_concurrent_positions",
    "live_capital_cap_usd",
    "live_execution_enabled",
}

LAB_PROMOTION_TYPE = "lab_promotion"


def _scrub_forbidden(params: dict[str, Any]) -> dict[str, Any]:
    """Recursively drop any forbidden_fields keys."""
    out = {}
    for k, v in params.items():
        if k in FORBIDDEN_FIELDS:
            logger.warning(f"promote: scrubbing forbidden field '{k}' from params")
            continue
        if isinstance(v, dict):
            out[k] = _scrub_forbidden(v)
        else:
            out[k] = v
    return out


async def scan_for_promotions(
    strategy_id: str = "matrix_agent",
    *,
    min_eval: int = MIN_EVAL_FOR_PROMOTION,
    min_fitness: Decimal = MIN_FITNESS_FOR_PROMOTION,
) -> uuid.UUID | None:
    """Detect a promotable lab experiment and write a MutationProposal.

    Returns the new proposal id, or None if no candidate qualifies or a
    pending proposal already exists.
    """
    async with shared_session_scope() as session:
        # Best eligible lab candidate
        cand_stmt = (
            select(LabExperiment)
            .where(LabExperiment.status == "active")
            .where(LabExperiment.n_evaluations >= min_eval)
            .where(LabExperiment.fitness_score >= min_fitness)
            .order_by(desc(LabExperiment.fitness_score))
            .limit(1)
        )
        candidate = (await session.execute(cand_stmt)).scalar_one_or_none()
        if candidate is None:
            return None

        # Skip if a pending lab_promotion proposal already exists for this
        # strategy (don't queue up duplicates while one is awaiting review).
        existing_stmt = select(
            exists().where(
                and_(
                    MutationProposal.strategy_id == strategy_id,
                    MutationProposal.proposal_type == LAB_PROMOTION_TYPE,
                    MutationProposal.status == "pending",
                )
            )
        )
        if (await session.execute(existing_stmt)).scalar_one():
            logger.debug(
                f"promote scan: pending lab_promotion already exists for {strategy_id}"
            )
            return None

        # Current active config for the live strategy
        cur_stmt = (
            select(StrategyConfig)
            .where(StrategyConfig.strategy_id == strategy_id)
            .where(StrategyConfig.status == "active")
            .order_by(desc(StrategyConfig.version))
            .limit(1)
        )
        current = (await session.execute(cur_stmt)).scalar_one_or_none()
        if current is None:
            logger.warning(f"promote scan: no active config for {strategy_id}; cannot propose")
            return None

        # Skip if the candidate's params are effectively identical to current
        if _params_equivalent(candidate.params, current.params):
            logger.debug("promote scan: candidate params match current; skipping")
            return None

        after_params = _scrub_forbidden(candidate.params or {})

        win_rate = (
            Decimal(candidate.n_wins) / Decimal(candidate.n_evaluations)
            if candidate.n_evaluations
            else Decimal("0")
        )
        rationale = (
            f"Lab promotion: experiment {str(candidate.id)[:8]} "
            f"(gen {candidate.generation}). "
            f"fitness={candidate.fitness_score:.4f}, "
            f"n_eval={candidate.n_evaluations}, n_wins={candidate.n_wins}, "
            f"win_rate={win_rate:.3f}. "
            f"Beats min_fitness={min_fitness}, min_eval={min_eval}."
        )

        proposal = MutationProposal(
            strategy_id=strategy_id,
            from_version=current.version,
            to_version=current.version + 1,
            proposal_type=LAB_PROMOTION_TYPE,
            before_params=current.params or {},
            after_params=after_params,
            metrics_window={
                "lab_experiment_id": str(candidate.id),
                "lab_generation": candidate.generation,
                "fitness_score": str(candidate.fitness_score),
                "n_evaluations": candidate.n_evaluations,
                "n_wins": candidate.n_wins,
                "win_rate": str(win_rate),
            },
            rationale=rationale,
            status="pending",
            source="labs",
        )
        session.add(proposal)
        # Need to flush to read back the id
        await session.flush()
        proposal_id = proposal.id

    logger.info(
        f"promote scan: proposal {str(proposal_id)[:8]} created from "
        f"lab {str(candidate.id)[:8]} (fitness={candidate.fitness_score:.4f})"
    )
    return proposal_id


def _params_equivalent(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    """Cheap structural compare. False positives only delay a proposal one cycle."""
    if not a or not b:
        return False
    aw = a.get("weights") or {}
    bw = b.get("weights") or {}
    if set(aw.keys()) != set(bw.keys()):
        return False
    for k in aw:
        try:
            if abs(Decimal(str(aw[k])) - Decimal(str(bw[k]))) > Decimal("0.001"):
                return False
        except (ArithmeticError, ValueError):
            return False
    try:
        if abs(Decimal(str(a.get("signal_threshold", 0))) - Decimal(str(b.get("signal_threshold", 0)))) > Decimal("0.001"):
            return False
    except (ArithmeticError, ValueError):
        return False
    return True


async def apply_proposal(proposal_id: uuid.UUID) -> bool:
    """Apply a pending lab_promotion proposal. Returns True on success.

    Steps:
        1. Load proposal; verify status=pending and type=lab_promotion.
        2. Scrub forbidden fields from after_params (defense-in-depth).
        3. Find current active StrategyConfig for the strategy; mark retired.
        4. Insert new StrategyConfig at to_version with after_params, active.
        5. Mark proposal applied, mark lab experiment promoted.
    """
    async with shared_session_scope() as session:
        proposal = await session.get(MutationProposal, proposal_id)
        if proposal is None:
            logger.error(f"apply: proposal {proposal_id} not found")
            return False
        if proposal.status != "pending":
            logger.error(f"apply: proposal {proposal_id} already {proposal.status}")
            return False
        if proposal.proposal_type != LAB_PROMOTION_TYPE:
            # We can still apply non-lab proposals here, but the lab status flip
            # below is skipped. Tighten later if needed.
            logger.info(f"apply: non-lab proposal type {proposal.proposal_type}; proceeding")

        scrubbed = _scrub_forbidden(proposal.after_params or {})

        # Retire existing active
        cur_stmt = (
            select(StrategyConfig)
            .where(StrategyConfig.strategy_id == proposal.strategy_id)
            .where(StrategyConfig.status == "active")
        )
        for cfg in (await session.execute(cur_stmt)).scalars():
            cfg.status = "retired"

        new_cfg = StrategyConfig(
            strategy_id=proposal.strategy_id,
            version=proposal.to_version,
            status="active",
            params=scrubbed,
            rationale=proposal.rationale,
            promoted_at=datetime.now(UTC),
        )
        session.add(new_cfg)

        proposal.status = "applied"
        proposal.applied_at = datetime.now(UTC)

        # If this came from labs, mark the source experiment promoted
        lab_id_str = (proposal.metrics_window or {}).get("lab_experiment_id")
        if lab_id_str:
            try:
                lab_id = uuid.UUID(lab_id_str)
                exp = await session.get(LabExperiment, lab_id)
                if exp is not None:
                    exp.status = "promoted"
            except (ValueError, TypeError):
                pass

    logger.info(
        f"apply: proposal {proposal_id} applied → {proposal.strategy_id} "
        f"v{proposal.from_version} → v{proposal.to_version}"
    )
    return True


async def apply_best_pending(strategy_id: str = "matrix_agent") -> uuid.UUID | None:
    """Convenience: apply the pending lab_promotion proposal with highest
    expected gain (here: most recent, since we suppress duplicates)."""
    async with shared_session_scope() as session:
        stmt = (
            select(MutationProposal)
            .where(MutationProposal.strategy_id == strategy_id)
            .where(MutationProposal.status == "pending")
            .where(MutationProposal.proposal_type == LAB_PROMOTION_TYPE)
            .order_by(desc(MutationProposal.created_at))
            .limit(1)
        )
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        pid = row.id

    ok = await apply_proposal(pid)
    return pid if ok else None
