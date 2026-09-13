"""_mutation_blocked: a live challenger or an unapplied proposal stops the
tick from spending an LLM call / writing duplicate proposals."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete

from matrix_shared import shared_session_scope
from matrix_shared.models import MutationProposal, StrategyConfig

from reflection.main import _mutation_blocked

pytestmark = pytest.mark.asyncio


async def _cfg(sid: str, version: int, status: str) -> StrategyConfig:
    async with shared_session_scope() as session:
        c = StrategyConfig(strategy_id=sid, asset_class="crypto", version=version, status=status,
                           params={"k": 1}, rationale="t")
        session.add(c)
        await session.flush()
        session.expunge(c)
    return c


async def _cleanup(sid: str) -> None:
    async with shared_session_scope() as session:
        await session.execute(delete(MutationProposal).where(MutationProposal.strategy_id == sid))
        await session.execute(delete(StrategyConfig).where(StrategyConfig.strategy_id == sid))


async def test_blocked_by_running_challenger_and_by_pending_proposal():
    sid = f"gate_{uuid.uuid4().hex[:8]}"
    try:
        champ = await _cfg(sid, 1, "active")
        assert await _mutation_blocked(champ) is None
        await _cfg(sid, 2, "shadow")
        assert (await _mutation_blocked(champ) or "").startswith("challenger v2")
        async with shared_session_scope() as session:
            await session.execute(delete(StrategyConfig).where(StrategyConfig.strategy_id == sid)
                                  .where(StrategyConfig.version == 2))
            session.add(MutationProposal(strategy_id=sid, asset_class="crypto", from_version=1, to_version=2,
                                         proposal_type="threshold_change", before_params={}, after_params={"k": 2},
                                         metrics_window={}, rationale="r", status="pending", source="agent"))
        assert "pending proposal" in (await _mutation_blocked(champ) or "")
    finally:
        await _cleanup(sid)
