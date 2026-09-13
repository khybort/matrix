"""reflection.efficacy — before/after PnL comparison + automatic rollback.

Pure tests for the verdict rule, DB-backed tests (shared postgres, uuid-scoped
strategy ids) for the pass itself.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from matrix_shared import shared_session_scope
from matrix_shared.models import MutationProposal, Outcome, Prediction, StrategyConfig

from reflection import efficacy as E

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------- pure rule

def _sample(pnls):
    return E.Sample.from_pnls([Decimal(str(x)) for x in pnls])


def test_verdict_pending_then_insufficient(monkeypatch):
    monkeypatch.setattr(E, "EFFICACY_MIN_N", 5)
    before, after = _sample([1, 1, 1]), _sample([1, 1])
    assert E.verdict_for(before, after, applied_age_h=1)[0] == "pending"
    assert E.verdict_for(before, after, applied_age_h=E.EFFICACY_MAX_HOURS + 1)[0] == "insufficient"


def test_verdict_negative_requires_loss_and_significance(monkeypatch):
    monkeypatch.setattr(E, "EFFICACY_MIN_N", 5)
    before = _sample([1.0, 1.2, 0.8, 1.1, 0.9, 1.0])
    worse = _sample([-1.0, -1.2, -0.8, -1.1, -0.9, -1.0])
    v, z = E.verdict_for(before, worse, applied_age_h=48)
    assert v == "negative" and z < -1
    # Worse but still profitable → not negative.
    milder = _sample([0.5, 0.4, 0.6, 0.5, 0.5, 0.4])
    v, _ = E.verdict_for(before, milder, applied_age_h=48)
    assert v in ("neutral", "negative") and v != "positive"
    assert E.verdict_for(before, milder, applied_age_h=48)[0] == "neutral"  # total > 0 blocks negative
    better = _sample([2.0, 2.2, 1.8, 2.1, 1.9, 2.0])
    assert E.verdict_for(before, better, applied_age_h=48)[0] == "positive"


def test_verdict_without_baseline_uses_sign(monkeypatch):
    monkeypatch.setattr(E, "EFFICACY_MIN_N", 3)
    assert E.verdict_for(_sample([]), _sample([-1, -1, -1]), applied_age_h=48)[0] == "negative"
    assert E.verdict_for(_sample([]), _sample([1, 1, 1]), applied_age_h=48)[0] == "neutral"


# ---------------------------------------------------------------- DB-backed

async def _seed(strategy_id: str, version: int, pnls: list[float], *, observed_at: datetime) -> None:
    pairs = []
    async with shared_session_scope() as session:
        for i, _ in enumerate(pnls):
            pid = uuid.uuid4()
            ts = observed_at - timedelta(seconds=300) + timedelta(seconds=i)
            pairs.append(pid)
            session.add(Prediction(
                id=pid, strategy_id=strategy_id, strategy_version=version, asset_class="crypto",
                symbol="BTCUSDT", exchange="bybit", side="long", confidence=Decimal("0.5"),
                horizon_seconds=60, generated_at=ts, close_by=ts + timedelta(seconds=60),
                entry_price_ref=Decimal("100"), status="closed",
            ))
    async with shared_session_scope() as session:
        for pid, pnl in zip(pairs, pnls, strict=True):
            session.add(Outcome(
                id=uuid.uuid4(), prediction_id=pid, asset_class="crypto",
                observed_at=observed_at + timedelta(seconds=1),
                pnl_usd=Decimal(str(pnl)), pnl_pct=Decimal("0.001") if pnl > 0 else Decimal("-0.001"),
                score=Decimal("0.1") if pnl > 0 else Decimal("-0.1"), reason="hit_horizon",
            ))


async def _cleanup(strategy_id: str) -> None:
    async with shared_session_scope() as session:
        pred_ids = list((await session.execute(
            select(Prediction.id).where(Prediction.strategy_id == strategy_id))).scalars())
        if pred_ids:
            await session.execute(delete(Outcome).where(Outcome.prediction_id.in_(pred_ids)))
            await session.execute(delete(Prediction).where(Prediction.id.in_(pred_ids)))
        await session.execute(delete(MutationProposal).where(MutationProposal.strategy_id == strategy_id))
        await session.execute(delete(StrategyConfig).where(StrategyConfig.strategy_id == strategy_id))


async def _setup(strategy_id: str, applied_at: datetime, *, active_version: int = 2) -> uuid.UUID:
    pid = uuid.uuid4()
    async with shared_session_scope() as session:
        session.add(StrategyConfig(strategy_id=strategy_id, asset_class="crypto", version=1,
                                   status="retired", params={"knob": 1, "other": "keep"}))
        session.add(StrategyConfig(strategy_id=strategy_id, asset_class="crypto", version=active_version,
                                   status="active", params={"knob": 2, "other": "keep"}))
        session.add(MutationProposal(
            id=pid, strategy_id=strategy_id, asset_class="crypto", from_version=1, to_version=2,
            proposal_type="param_tune", before_params={"knob": 1}, after_params={"knob": 2},
            metrics_window={"applied_version": 2}, rationale="test", status="applied",
            applied_at=applied_at, source="rule",
        ))
    return pid


async def test_negative_efficacy_rolls_back_and_guards_reproposal(monkeypatch):
    monkeypatch.setattr(E, "EFFICACY_MIN_N", 5)
    sid = f"eff_{uuid.uuid4().hex[:6]}"
    now = datetime.now(UTC)
    applied_at = now - timedelta(hours=48)
    try:
        pid = await _setup(sid, applied_at)
        await _seed(sid, 1, [1.0, 1.1, 0.9, 1.0, 1.2, 0.8], observed_at=applied_at - timedelta(hours=2))
        await _seed(sid, 2, [-1.0, -1.1, -0.9, -1.0, -1.2, -0.8], observed_at=applied_at + timedelta(hours=2))

        counts = await E.evaluate_applied_proposals(now=now, strategy_id=sid)
        assert counts.get("negative") == 1 and counts.get("rolled_back") == 1

        async with shared_session_scope() as session:
            prop = await session.get(MutationProposal, pid)
            assert prop.status == "reverted"
            eff = prop.metrics_window["efficacy"]
            assert eff["verdict"] == "negative" and eff["rolled_back"] is True
            assert eff["rollback_version"] == 3
            active = (await session.execute(
                select(StrategyConfig).where(StrategyConfig.strategy_id == sid)
                .where(StrategyConfig.status == "active"))).scalar_one()
            assert active.version == 3
            assert active.params == {"knob": 1, "other": "keep"}  # restored, unrelated key kept
            rb = (await session.execute(
                select(MutationProposal).where(MutationProposal.strategy_id == sid)
                .where(MutationProposal.proposal_type == "rollback"))).scalar_one()
            assert rb.status == "applied" and rb.source == "efficacy"
            assert rb.metrics_window["rolled_back_proposal_id"] == str(pid)

        # Oscillation guard: the reverted after_params are blocked for the cooldown.
        assert await E.recently_reverted(sid, "crypto", {"knob": 2}) is True
        assert await E.recently_reverted(sid, "crypto", {"knob": 3}) is False
        # Second pass is idempotent (final verdict evaluated once).
        assert await E.evaluate_applied_proposals(now=now, strategy_id=sid) == {}
    finally:
        await _cleanup(sid)


async def test_pending_when_not_enough_after_outcomes(monkeypatch):
    monkeypatch.setattr(E, "EFFICACY_MIN_N", 50)
    sid = f"eff_{uuid.uuid4().hex[:6]}"
    now = datetime.now(UTC)
    applied_at = now - timedelta(hours=48)
    try:
        pid = await _setup(sid, applied_at)
        await _seed(sid, 2, [-1.0, -1.0], observed_at=applied_at + timedelta(hours=1))
        counts = await E.evaluate_applied_proposals(now=now, strategy_id=sid)
        assert counts == {"pending": 1}
        async with shared_session_scope() as session:
            prop = await session.get(MutationProposal, pid)
            assert prop.status == "applied"
            assert prop.metrics_window["efficacy"]["verdict"] == "pending"
            active = (await session.execute(
                select(StrategyConfig).where(StrategyConfig.strategy_id == sid)
                .where(StrategyConfig.status == "active"))).scalar_one()
            assert active.version == 2
    finally:
        await _cleanup(sid)


async def test_too_young_proposals_are_not_evaluated():
    sid = f"eff_{uuid.uuid4().hex[:6]}"
    now = datetime.now(UTC)
    try:
        await _setup(sid, now - timedelta(hours=1))
        assert await E.evaluate_applied_proposals(now=now, strategy_id=sid) == {}
    finally:
        await _cleanup(sid)


# ---------------------------------------------------------------- challengers

def test_challenger_verdict_rule(monkeypatch):
    monkeypatch.setattr(E, "CHALLENGER_MIN_N", 5)
    champ = _sample([0.1, 0.2, 0.0, 0.1, 0.2, 0.1])
    better = _sample([1.0, 1.1, 0.9, 1.2, 1.0, 1.1])
    worse = _sample([-1.0, -1.1, -0.9, -1.2, -1.0, -1.1])
    assert E.challenger_verdict(champ, better, age_h=48) == "cutover"
    assert E.challenger_verdict(champ, worse, age_h=48) == "retire"
    assert E.challenger_verdict(champ, _sample([0.1, 0.1]), age_h=48) == "pending"
    assert E.challenger_verdict(champ, _sample([0.1, 0.1]), age_h=E.CHALLENGER_MAX_HOURS) == "retire"
    same = _sample([0.1, 0.2, 0.0, 0.1, 0.2, 0.1])
    assert E.challenger_verdict(champ, same, age_h=48) == "pending"


async def test_challenger_cutover_and_retire(monkeypatch):
    monkeypatch.setattr(E, "CHALLENGER_MIN_N", 5)
    sid = f"chal_{uuid.uuid4().hex[:6]}"
    now = datetime.now(UTC)
    started = now - timedelta(hours=30)
    try:
        async with shared_session_scope() as session:
            session.add(StrategyConfig(strategy_id=sid, asset_class="crypto", version=1, status="active",
                                       params={"knob": 1}))
            session.add(StrategyConfig(strategy_id=sid, asset_class="crypto", version=2, status="shadow",
                                       params={"knob": 2}, promoted_at=started))
        await _seed(sid, 1, [0.1, 0.0, 0.2, 0.1, 0.0, 0.1], observed_at=started + timedelta(hours=1))
        await _seed(sid, 2, [1.0, 1.1, 0.9, 1.2, 1.0, 1.1], observed_at=started + timedelta(hours=1))
        counts = await E.evaluate_challengers(now=now, strategy_id=sid)
        assert counts == {"cutover": 1}
        async with shared_session_scope() as session:
            rows = {r.version: r.status for r in (await session.execute(
                select(StrategyConfig).where(StrategyConfig.strategy_id == sid))).scalars()}
            assert rows == {1: "retired", 2: "active"}
            prop = (await session.execute(select(MutationProposal)
                    .where(MutationProposal.strategy_id == sid))).scalar_one()
            assert prop.proposal_type == "cutover" and prop.status == "applied"
            assert prop.metrics_window["challenger"]["version"] == 2

        # A losing challenger against the new champion gets retired.
        async with shared_session_scope() as session:
            session.add(StrategyConfig(strategy_id=sid, asset_class="crypto", version=3, status="shadow",
                                       params={"knob": 3}, promoted_at=started))
        await _seed(sid, 3, [-1.0, -1.1, -0.9, -1.2, -1.0, -1.1], observed_at=started + timedelta(hours=2))
        counts = await E.evaluate_challengers(now=now, strategy_id=sid)
        assert counts == {"retire": 1}
        async with shared_session_scope() as session:
            v3 = (await session.execute(select(StrategyConfig).where(StrategyConfig.strategy_id == sid)
                                        .where(StrategyConfig.version == 3))).scalar_one()
            assert v3.status == "retired"
            v2 = (await session.execute(select(StrategyConfig).where(StrategyConfig.strategy_id == sid)
                                        .where(StrategyConfig.version == 2))).scalar_one()
            assert v2.status == "active"
    finally:
        await _cleanup(sid)


async def test_repeated_negatives_file_one_dev_task(monkeypatch):
    monkeypatch.setattr(E, "DEV_TASK_NEGATIVE_THRESHOLD", 2)
    sid = f"esc_{uuid.uuid4().hex[:6]}"
    now = datetime.now(UTC)
    from sqlalchemy import text
    try:
        async with shared_session_scope() as session:
            for i in range(2):
                session.add(MutationProposal(
                    strategy_id=sid, asset_class="crypto", from_version=i + 1, to_version=i + 2,
                    proposal_type="param_tune", before_params={}, after_params={"k": i},
                    metrics_window={"efficacy": {"verdict": "negative"}}, rationale="r",
                    status="reverted", source="rule",
                ))
        assert await E.maybe_file_dev_task(sid, "crypto", now=now) is not None
        assert await E.maybe_file_dev_task(sid, "crypto", now=now) is None  # deduped
        async with shared_session_scope() as session:
            rows = (await session.execute(text(
                "SELECT description, source::text, touches_files FROM dev_tasks WHERE description LIKE :m"
            ), {"m": f"%[efficacy:{sid}/crypto]%"})).all()
            assert len(rows) == 1 and rows[0][1] == "reflection"
            assert "services/strategy/src/strategy/modules/crypto/" in rows[0][2]
    finally:
        async with shared_session_scope() as session:
            await session.execute(text("DELETE FROM dev_tasks WHERE description LIKE :m"),
                                  {"m": f"%[efficacy:{sid}/crypto]%"})
        await _cleanup(sid)
