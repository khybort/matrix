"""Mutation efficacy: did an applied proposal actually help, and if not, undo it.

Closes the loop that docs/AUTONOMY_PLAN.md §5 found missing — 2,671
proposals were auto-applied in June 2026 and nothing ever compared PnL
before vs after. This module runs inside the reflection tick:

  1. For every `applied` proposal (not slot_adjustment / rollback) old enough
     (`EFFICACY_MIN_HOURS`), compare realised outcome PnL of the version it
     replaced (window before `applied_at`) with the version it created
     (`metrics_window.applied_version`, falling back to `to_version`).
  2. Verdict by a two-sample z on mean pnl_usd per outcome:
        pending      n_after < EFFICACY_MIN_N (re-checked next tick)
        insufficient still < MIN_N after EFFICACY_MAX_HOURS → stop checking
        negative     total_after < 0 AND z <= -Z_NEG (significantly worse)
        positive     z >= +Z_POS
        neutral      otherwise
     stored in `metrics_window.efficacy` (JSON, no schema change).
  3. `negative` while that version is still the active config → automatic
     ROLLBACK: retire the active row, insert a new version carrying the
     proposal's `before_params` (merged over current params so unrelated keys
     survive), write an auditable `rollback` proposal (source `efficacy`,
     status `applied`) and mark the original `reverted`.
  4. `recently_reverted(...)` lets the proposer skip re-proposing the same
     `after_params` for REVERT_COOLDOWN_DAYS, so rollback ↔ re-apply can't
     oscillate.

Risk caps are never touched: rollback only moves `strategy_configs.params`
between two snapshots the system itself already ran.
"""

from __future__ import annotations

import json
import math
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from loguru import logger
from matrix_shared import local_session_scope, shared_session_scope
from matrix_shared.edge_study import episode_pnls
from matrix_shared.models import MutationProposal, Outcome, Prediction, StrategyConfig
from sqlalchemy import desc, func, select

EFFICACY_MIN_HOURS = float(os.environ.get("MATRIX_EFFICACY_MIN_HOURS", "24"))
EFFICACY_MAX_HOURS = float(os.environ.get("MATRIX_EFFICACY_MAX_HOURS", "336"))  # 14d
EFFICACY_MIN_N = int(os.environ.get("MATRIX_EFFICACY_MIN_N", "50"))
EFFICACY_BEFORE_WINDOW_HOURS = float(os.environ.get("MATRIX_EFFICACY_BEFORE_WINDOW_HOURS", "168"))
Z_NEG = float(os.environ.get("MATRIX_EFFICACY_Z_NEG", "1.0"))
# "No difference detected" is a result, not a reason to keep measuring. A
# challenger that has matched its champion over a decent sample is occupying
# the one challenger slot its strategy gets, and the queue behind it may hold a
# change with real evidence — on 2026-09-20 a vol-filter tweak (|z|=0.08 after
# 106 outcomes) was set to block a horizon change measured at t=3.59 for
# another week. Retire the tie and let the next proposal run.
INDIFFERENCE_Z = float(os.environ.get("MATRIX_CHALLENGER_INDIFFERENCE_Z", "0.5"))
INDIFFERENCE_MIN_N = int(os.environ.get("MATRIX_CHALLENGER_INDIFFERENCE_MIN_N", "100"))
Z_POS = float(os.environ.get("MATRIX_EFFICACY_Z_POS", "1.0"))
REVERT_COOLDOWN_DAYS = float(os.environ.get("MATRIX_EFFICACY_REVERT_COOLDOWN_DAYS", "7"))
AUTO_ROLLBACK = os.environ.get("MATRIX_EFFICACY_AUTO_ROLLBACK", "true").strip().lower() != "false"
# Bound the per-tick work: the June-2026 backlog is ~2,700 applied proposals and
# the outcome join has no version index; 25/tick drains it in a day.
MAX_PER_TICK = int(os.environ.get("MATRIX_EFFICACY_MAX_PER_TICK", "25"))
# When parameter tuning keeps failing, escalate to code: after this many
# negative verdicts (rollbacks / retired challengers) for one strategy within
# DEV_TASK_WINDOW_DAYS, file a dev_agent task to rework the strategy logic.
DEV_TASK_NEGATIVE_THRESHOLD = int(os.environ.get("MATRIX_EFFICACY_DEV_TASK_NEGATIVES", "3"))
DEV_TASK_WINDOW_DAYS = float(os.environ.get("MATRIX_EFFICACY_DEV_TASK_WINDOW_DAYS", "14"))
DEV_TASK_ENABLED = os.environ.get("MATRIX_EFFICACY_DEV_TASKS", "true").strip().lower() != "false"

_SKIP_TYPES = frozenset({"slot_adjustment", "rollback"})


@dataclass(slots=True)
class Sample:
    n: int
    mean: float
    var: float
    total: float

    @classmethod
    def from_pnls(cls, pnls: list[Decimal] | list[float]) -> "Sample":
        n = len(pnls)
        if n == 0:
            return cls(0, 0.0, 0.0, 0.0)
        xs = [float(p) for p in pnls]
        mean = sum(xs) / n
        var = sum((x - mean) ** 2 for x in xs) / (n - 1) if n > 1 else 0.0
        return cls(n, mean, var, sum(xs))


def verdict_for(before: Sample, after: Sample, *, applied_age_h: float) -> tuple[str, float | None]:
    """Pure decision rule. Returns (verdict, z)."""
    if after.n < EFFICACY_MIN_N:
        return ("insufficient" if applied_age_h >= EFFICACY_MAX_HOURS else "pending"), None
    if before.n < 2:
        # Nothing to compare against — judge the new version on its own sign.
        z = None
        if after.total < 0:
            return "negative", z
        return "neutral", z
    se = math.sqrt(before.var / before.n + after.var / after.n)
    z = (after.mean - before.mean) / se if se > 0 else 0.0
    if after.total < 0 and z <= -Z_NEG:
        return "negative", z
    if z >= Z_POS:
        return "positive", z
    return "neutral", z


async def _sample(
    session, strategy_id: str, asset_class: str, version: int, since: datetime, until: datetime | None
) -> Sample:
    """Realised PnL per independent bet for one version.

    Counted in episodes (`edge_study.episode_pnls`), not outcomes: a version
    that re-emits the same (symbol, side) inside its horizon and gets filled
    two or three times has made one bet, and scoring each fill as its own
    sample shrank the z-test's standard error by the square root of the
    duplication — funding_reversion's 3 101 closed fills (Sep–Oct 2026) were
    473 bets. That made rollbacks and challenger verdicts fire on a fraction
    of the evidence they claimed.
    """
    stmt = (
        select(
            Outcome.pnl_usd,
            Prediction.strategy_id,
            Prediction.asset_class,
            Prediction.symbol,
            Prediction.side,
            Prediction.generated_at,
            Prediction.horizon_seconds,
        )
        .join(Prediction, Prediction.id == Outcome.prediction_id)
        .where(Prediction.strategy_id == strategy_id)
        .where(Prediction.asset_class == asset_class)
        .where(Prediction.strategy_version == version)
        .where(Outcome.observed_at >= since)
        .where(Outcome.reason != "orphan_flat_close")
    )
    if until is not None:
        stmt = stmt.where(Outcome.observed_at < until)
    rows = [r._asdict() for r in (await session.execute(stmt.order_by(Prediction.generated_at))).all()]
    return Sample.from_pnls(episode_pnls(rows))


def _normalize(params: dict[str, Any] | None) -> str:
    return json.dumps(params or {}, sort_keys=True, default=str)


async def recently_reverted(
    strategy_id: str, asset_class: str, after_params: dict[str, Any], *, days: float = REVERT_COOLDOWN_DAYS
) -> bool:
    """True when the same after_params were rolled back for this strategy recently."""
    since = datetime.now(UTC) - timedelta(days=days)
    target = _normalize(after_params)
    async with shared_session_scope() as session:
        rows = (await session.execute(
            select(MutationProposal.after_params)
            .where(MutationProposal.strategy_id == strategy_id)
            .where(MutationProposal.asset_class == asset_class)
            .where(MutationProposal.status == "reverted")
            .where(MutationProposal.updated_at >= since)
        )).all()
    return any(_normalize(r[0]) == target for r in rows)


async def _rollback(session, proposal: MutationProposal, active: StrategyConfig, efficacy: dict) -> int:
    restored = dict(active.params or {})
    restored.update(proposal.before_params or {})
    max_v = (await session.execute(
        select(func.max(StrategyConfig.version))
        .where(StrategyConfig.strategy_id == proposal.strategy_id)
        .where(StrategyConfig.asset_class == proposal.asset_class)
    )).scalar() or 0
    new_version = int(max_v) + 1
    active.status = "retired"
    now = datetime.now(UTC)
    session.add(StrategyConfig(
        strategy_id=proposal.strategy_id,
        asset_class=proposal.asset_class,
        version=new_version,
        status="active",
        params=restored,
        rationale=(
            f"efficacy rollback of proposal {proposal.id} (v{active.version}): "
            f"after n={efficacy['after']['n']} mean={efficacy['after']['mean']:.4f} "
            f"vs before mean={efficacy['before']['mean']:.4f}, z={efficacy.get('z')}"
        ),
        promoted_at=now,
    ))
    session.add(MutationProposal(
        strategy_id=proposal.strategy_id,
        asset_class=proposal.asset_class,
        from_version=active.version,
        to_version=new_version,
        proposal_type="rollback",
        before_params=dict(active.params or {}),
        after_params=restored,
        metrics_window={
            "efficacy": efficacy,
            "rolled_back_proposal_id": str(proposal.id),
            "applied_version": new_version,
        },
        rationale=f"auto-rollback: proposal {proposal.id} measured negative efficacy",
        status="applied",
        applied_at=now,
        source="efficacy",
    ))
    return new_version


async def evaluate_applied_proposals(
    *, now: datetime | None = None, strategy_id: str | None = None
) -> dict[str, int]:
    """One efficacy pass (at most MAX_PER_TICK proposals). Returns counts by
    verdict (+ 'rolled_back'). `strategy_id` narrows the scan (tests / CLI)."""
    now = now or datetime.now(UTC)
    counts: dict[str, int] = {}
    evaluated = 0
    escalate: list[tuple[str, str]] = []
    async with shared_session_scope() as session:
        stmt = (
            select(MutationProposal)
            .where(MutationProposal.status == "applied")
            .where(MutationProposal.applied_at.isnot(None))
            .where(MutationProposal.applied_at <= now - timedelta(hours=EFFICACY_MIN_HOURS))
            .order_by(MutationProposal.applied_at)
        )
        if strategy_id is not None:
            stmt = stmt.where(MutationProposal.strategy_id == strategy_id)
        proposals = list((await session.execute(stmt)).scalars())

    for p in proposals:
        if p.proposal_type in _SKIP_TYPES:
            continue
        mw = dict(p.metrics_window or {})
        if mw.get("challenger"):
            continue  # judged by evaluate_challengers (champion vs shadow), not before/after
        prev = mw.get("efficacy") or {}
        if prev.get("verdict") in ("positive", "neutral", "negative", "insufficient"):
            continue  # final verdicts are evaluated once
        if evaluated >= MAX_PER_TICK:
            break
        evaluated += 1
        applied_at = p.applied_at if p.applied_at.tzinfo else p.applied_at.replace(tzinfo=UTC)
        after_version = int(mw.get("applied_version") or p.to_version)
        age_h = (now - applied_at).total_seconds() / 3600.0
        try:
            async with shared_session_scope() as session:
                before = await _sample(
                    session, p.strategy_id, p.asset_class, p.from_version,
                    applied_at - timedelta(hours=EFFICACY_BEFORE_WINDOW_HOURS), applied_at,
                )
                after = await _sample(
                    session, p.strategy_id, p.asset_class, after_version, applied_at, None,
                )
            verdict, z = verdict_for(before, after, applied_age_h=age_h)
            efficacy = {
                "verdict": verdict,
                "z": round(z, 3) if z is not None else None,
                "before": {"n": before.n, "mean": round(before.mean, 6), "total": round(before.total, 4),
                           "version": p.from_version},
                "after": {"n": after.n, "mean": round(after.mean, 6), "total": round(after.total, 4),
                          "version": after_version},
                "evaluated_at": now.isoformat(),
            }
            counts[verdict] = counts.get(verdict, 0) + 1

            async with shared_session_scope() as session:
                row = await session.get(MutationProposal, p.id)
                if row is None:
                    continue
                if verdict == "negative" and AUTO_ROLLBACK:
                    active = (await session.execute(
                        select(StrategyConfig)
                        .where(StrategyConfig.strategy_id == p.strategy_id)
                        .where(StrategyConfig.asset_class == p.asset_class)
                        .where(StrategyConfig.status == "active")
                        .order_by(desc(StrategyConfig.version))
                        .limit(1)
                    )).scalar_one_or_none()
                    if active is not None and active.version == after_version:
                        new_v = await _rollback(session, row, active, efficacy)
                        efficacy["rolled_back"] = True
                        efficacy["rollback_version"] = new_v
                        row.status = "reverted"
                        counts["rolled_back"] = counts.get("rolled_back", 0) + 1
                        escalate.append((p.strategy_id, p.asset_class))
                        logger.warning(
                            f"efficacy: ROLLED BACK {p.strategy_id}/{p.asset_class} "
                            f"v{after_version} → v{new_v} (proposal {p.id}, z={efficacy['z']})"
                        )
                    else:
                        efficacy["rolled_back"] = False
                        efficacy["note"] = "version no longer active; nothing to roll back"
                mw["efficacy"] = efficacy
                row.metrics_window = mw  # reassign: JSON column has no mutation tracking
        except Exception as e:  # noqa: BLE001 — one proposal must not break the pass
            logger.exception(f"efficacy: proposal {p.id} evaluation failed: {e}")

    for sid, ac in dict.fromkeys(escalate):
        try:
            await maybe_file_dev_task(sid, ac, now=now)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"efficacy: dev task escalation failed for {sid}/{ac}: {e}")
    if counts:
        logger.info("efficacy: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return counts


# --------------------------------------------------------------- challengers

CHALLENGER_MIN_N = int(os.environ.get("MATRIX_CHALLENGER_MIN_N", "50"))
CHALLENGER_MAX_HOURS = float(os.environ.get("MATRIX_CHALLENGER_MAX_HOURS", "336"))  # 14d


def challenger_verdict(champion: Sample, challenger: Sample, *, age_h: float) -> str:
    """cutover | retire | pending.

    cutover: enough samples, challenger significantly better (z >= Z_POS) and
             not loss-making.
    retire:  significantly worse, time is up without a win, or the two are
             indistinguishable after a decent sample — a tie is an answer, and
             the slot is worth more to the next proposal than another week of
             confirming nothing.
    pending: still genuinely undecided.
    """
    if challenger.n < CHALLENGER_MIN_N or champion.n < 2:
        return "retire" if age_h >= CHALLENGER_MAX_HOURS else "pending"
    se = math.sqrt(champion.var / champion.n + challenger.var / challenger.n)
    z = (challenger.mean - champion.mean) / se if se > 0 else 0.0
    if z >= Z_POS and challenger.total > 0 and challenger.mean > champion.mean:
        return "cutover"
    if z <= -Z_NEG or age_h >= CHALLENGER_MAX_HOURS:
        return "retire"
    if (
        abs(z) < INDIFFERENCE_Z
        and challenger.n >= INDIFFERENCE_MIN_N
        and champion.n >= INDIFFERENCE_MIN_N
    ):
        return "retire"  # measured, and it changed nothing — free the slot
    return "pending"


async def evaluate_challengers(*, now: datetime | None = None, strategy_id: str | None = None) -> dict[str, int]:
    """Compare every `shadow` config against its active champion since the
    challenger started; cut over or retire it. Returns counts by verdict."""
    now = now or datetime.now(UTC)
    counts: dict[str, int] = {}
    retired_for: list[tuple[str, str]] = []
    async with shared_session_scope() as session:
        stmt = select(StrategyConfig).where(StrategyConfig.status == "shadow")
        if strategy_id is not None:
            stmt = stmt.where(StrategyConfig.strategy_id == strategy_id)
        shadows = list((await session.execute(stmt)).scalars())

    for sh in shadows:
        started = sh.promoted_at or sh.created_at
        started = started if started.tzinfo else started.replace(tzinfo=UTC)
        age_h = (now - started).total_seconds() / 3600.0
        try:
            async with shared_session_scope() as session:
                champ = (await session.execute(
                    select(StrategyConfig)
                    .where(StrategyConfig.strategy_id == sh.strategy_id)
                    .where(StrategyConfig.asset_class == sh.asset_class)
                    .where(StrategyConfig.status == "active")
                    .order_by(desc(StrategyConfig.version)).limit(1)
                )).scalar_one_or_none()
                if champ is None:
                    continue
                c_s = await _sample(session, sh.strategy_id, sh.asset_class, champ.version, started, None)
                s_s = await _sample(session, sh.strategy_id, sh.asset_class, sh.version, started, None)
            verdict = challenger_verdict(c_s, s_s, age_h=age_h)
            counts[verdict] = counts.get(verdict, 0) + 1
            if verdict == "pending":
                continue
            snapshot = {
                "verdict": verdict,
                "champion": {"version": champ.version, "n": c_s.n, "mean": round(c_s.mean, 6), "total": round(c_s.total, 4)},
                "challenger": {"version": sh.version, "n": s_s.n, "mean": round(s_s.mean, 6), "total": round(s_s.total, 4)},
                "since": started.isoformat(), "evaluated_at": now.isoformat(),
            }
            async with shared_session_scope() as session:
                sh_row = await session.get(StrategyConfig, sh.id)
                ch_row = await session.get(StrategyConfig, champ.id)
                if sh_row is None or ch_row is None or sh_row.status != "shadow":
                    continue
                if verdict == "cutover":
                    ch_row.status = "retired"
                    sh_row.status = "active"
                    sh_row.promoted_at = now
                    ptype, pstatus = "cutover", "applied"
                    logger.warning(
                        f"efficacy: CUTOVER {sh.strategy_id}/{sh.asset_class} v{champ.version} → "
                        f"v{sh.version} (challenger mean {s_s.mean:.4f} vs {c_s.mean:.4f}, n={s_s.n})"
                    )
                else:
                    sh_row.status = "retired"
                    ptype, pstatus = "challenger_retired", "rejected"
                    retired_for.append((sh.strategy_id, sh.asset_class))
                    logger.info(
                        f"efficacy: retired challenger {sh.strategy_id}/{sh.asset_class} v{sh.version} "
                        f"(n={s_s.n}, mean {s_s.mean:.4f} vs champion {c_s.mean:.4f}, age {age_h:.0f}h)"
                    )
                session.add(MutationProposal(
                    strategy_id=sh.strategy_id, asset_class=sh.asset_class,
                    from_version=champ.version, to_version=sh.version,
                    proposal_type=ptype, before_params=dict(champ.params or {}),
                    after_params=dict(sh.params or {}),
                    metrics_window={**snapshot, "applied_version": sh.version},
                    rationale=f"challenger evaluation: {verdict}", status=pstatus,
                    applied_at=now if pstatus == "applied" else None, source="efficacy",
                ))
        except Exception as e:  # noqa: BLE001
            logger.exception(f"efficacy: challenger {sh.strategy_id} v{sh.version} evaluation failed: {e}")
    for sid, ac in dict.fromkeys(retired_for):
        try:
            await maybe_file_dev_task(sid, ac, now=now)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"efficacy: dev task escalation failed for {sid}/{ac}: {e}")
    if counts:
        logger.info("challengers: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return counts


# ------------------------------------------------------- escalate to dev_agent

async def maybe_file_dev_task(strategy_id: str, asset_class: str, *, now: datetime | None = None) -> int | None:
    """Parameter tuning has failed repeatedly → ask dev_agent to rework the code.

    Counts `reverted` proposals + `challenger_retired` rows for the strategy in
    the window; at DEV_TASK_NEGATIVE_THRESHOLD files ONE `dev_tasks` row
    (source `reflection`, marker `[efficacy:<sid>/<ac>]`, deduped for the
    window). Returns the new task id or None.
    """
    if not DEV_TASK_ENABLED:
        return None
    from sqlalchemy import text as _text
    now = now or datetime.now(UTC)
    since = now - timedelta(days=DEV_TASK_WINDOW_DAYS)
    marker = f"[efficacy:{strategy_id}/{asset_class}]"
    async with shared_session_scope() as session:
        negatives = (await session.execute(
            select(func.count(MutationProposal.id))
            .where(MutationProposal.strategy_id == strategy_id)
            .where(MutationProposal.asset_class == asset_class)
            .where(MutationProposal.updated_at >= since)
            .where(
                (MutationProposal.status == "reverted")
                | (MutationProposal.proposal_type == "challenger_retired")
            )
        )).scalar_one()
        if int(negatives or 0) < DEV_TASK_NEGATIVE_THRESHOLD:
            return None
        recent = (await session.execute(
            select(MutationProposal.proposal_type, MutationProposal.rationale, MutationProposal.metrics_window)
            .where(MutationProposal.strategy_id == strategy_id)
            .where(MutationProposal.asset_class == asset_class)
            .where(MutationProposal.updated_at >= since)
            .order_by(desc(MutationProposal.updated_at)).limit(5)
        )).all()
        evidence = "\n".join(
            f"- {ptype}: {str(rat)[:160]} | efficacy={json.dumps((mw or {}).get('efficacy') or (mw or {}).get('challenger'), default=str)[:200]}"
            for ptype, rat, mw in recent
        )
        description = (
            f"{marker} Strategy `{strategy_id}` ({asset_class}) keeps losing after parameter "
            f"mutations: {negatives} negative efficacy verdicts in {DEV_TASK_WINDOW_DAYS:.0f} days "
            f"(rollbacks / retired challengers). Parameter tuning is exhausted — review the signal "
            f"logic itself. Read the strategy module under services/strategy/src/strategy/modules/"
            f"{asset_class}/ (or services/agent for matrix_agent), the recent outcomes for it, and "
            f"either fix a concrete defect in how the signal is computed or add a guard that stops "
            f"it trading in the regime where it loses. Keep the change small and covered by tests; "
            f"do not touch risk caps or the live-capital gate files.\n\nRecent evidence:\n{evidence}"
        )
    # dev_tasks is a LOCAL-tier table (dev_agent reads LOCAL_DATABASE_URL); the
    # SHARED copy is an empty migration artefact where filed tasks were stranded.
    async with local_session_scope() as session:
        dup = (await session.execute(_text(
            "SELECT id FROM dev_tasks WHERE description LIKE :m AND created_at >= :since LIMIT 1"
        ), {"m": f"%{marker}%", "since": since})).scalar()
        if dup is not None:
            return None
        task_id = (await session.execute(_text(
            "INSERT INTO dev_tasks (source, description, priority, touches_files, run_tests, "
            " review_mode, auto_commit) "
            "VALUES ('reflection', :d, 3, :tf, TRUE, 'auto', FALSE) RETURNING id"
        ), {"d": description, "tf": [f"services/strategy/src/strategy/modules/{asset_class}/"]})).scalar()
    logger.warning(f"efficacy: filed dev task #{task_id} for {strategy_id}/{asset_class} ({negatives} negatives)")
    return int(task_id) if task_id is not None else None
