"""apply_proposal(as_shadow=True) creates a challenger instead of cutting over."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from matrix_shared import shared_session_scope
from matrix_shared.models import MutationProposal, StrategyConfig

from labs.promote import apply_proposal

pytestmark = pytest.mark.asyncio


async def _cleanup(sid: str) -> None:
    async with shared_session_scope() as session:
        await session.execute(delete(MutationProposal).where(MutationProposal.strategy_id == sid))
        await session.execute(delete(StrategyConfig).where(StrategyConfig.strategy_id == sid))


async def _proposal(sid: str, after: dict) -> uuid.UUID:
    pid = uuid.uuid4()
    async with shared_session_scope() as session:
        session.add(MutationProposal(
            id=pid, strategy_id=sid, asset_class="crypto", from_version=1, to_version=2,
            proposal_type="param_tune", before_params={"knob": 1}, after_params=after,
            metrics_window={}, rationale="t", status="pending", source="rule",
        ))
    return pid


async def test_shadow_apply_keeps_champion_and_blocks_second_challenger():
    sid = f"chal_{uuid.uuid4().hex[:6]}"
    try:
        async with shared_session_scope() as session:
            session.add(StrategyConfig(strategy_id=sid, asset_class="crypto", version=1,
                                       status="active", params={"knob": 1, "keep": "x"}))
        p1 = await _proposal(sid, {"knob": 2})
        assert await apply_proposal(p1, as_shadow=True) is True
        async with shared_session_scope() as session:
            rows = {r.version: (r.status, r.params) for r in (await session.execute(
                select(StrategyConfig).where(StrategyConfig.strategy_id == sid))).scalars()}
            assert rows[1][0] == "active"
            assert rows[2] == ("shadow", {"knob": 2, "keep": "x"})
            prop = await session.get(MutationProposal, p1)
            assert prop.status == "applied"
            assert prop.metrics_window == {"applied_version": 2, "challenger": True}

        p2 = await _proposal(sid, {"knob": 3})
        assert await apply_proposal(p2, as_shadow=True) is False  # one challenger at a time
        async with shared_session_scope() as session:
            assert (await session.get(MutationProposal, p2)).status == "pending"
    finally:
        await _cleanup(sid)
