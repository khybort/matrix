"""Integration tests for the stale-proposal sweep step in _tick."""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import select, text

TEST_SHARED_DSN = os.environ.get(
    "REFLECTION_TEST_SHARED_DSN",
    "postgres://matrix:matrix_dev_only@localhost:5433/matrix_shared",
)
os.environ.setdefault("SHARED_DATABASE_URL", TEST_SHARED_DSN)
os.environ.setdefault("LOCAL_DATABASE_URL", TEST_SHARED_DSN)

from matrix_shared import shared_session_scope
from matrix_shared.models import MutationProposal


async def _insert_proposal(strategy_id: str, age_days: int) -> uuid.UUID:
    """Insert a pending MutationProposal with created_at offset by age_days."""
    prop_id = uuid.uuid4()
    async with shared_session_scope() as session:
        await session.execute(
            text(
                "INSERT INTO mutation_proposals "
                "(id, strategy_id, asset_class, from_version, to_version, "
                " proposal_type, before_params, after_params, metrics_window, "
                " rationale, status, source, created_at, updated_at) "
                "VALUES "
                "(:id, :sid, 'crypto', 1, 2, 'weight_tune', '{}', '{}', '{}', "
                " 'test', 'pending', 'rule', "
                " now() - :age * interval '1 day', "
                " now() - :age * interval '1 day')"
            ),
            {"id": prop_id, "sid": strategy_id, "age": age_days},
        )
    return prop_id


async def _cleanup(strategy_id: str) -> None:
    async with shared_session_scope() as session:
        await session.execute(
            text("DELETE FROM mutation_proposals WHERE strategy_id = :sid"),
            {"sid": strategy_id},
        )


@pytest.mark.asyncio
async def test_sweep_marks_old_pending_as_superseded():
    """A pending proposal older than 7 days must be set to 'superseded' by _tick."""
    strategy_id = f"TEST_sweep_{uuid.uuid4().hex[:6]}"
    prop_id = await _insert_proposal(strategy_id, age_days=8)
    try:
        from reflection.main import _tick
        await _tick(window_hours=24, use_llm=False, min_outcomes=10, score_trigger=-0.05)

        async with shared_session_scope() as session:
            proposal = await session.get(MutationProposal, prop_id)
        assert proposal is not None
        assert proposal.status == "superseded", (
            f"Expected 'superseded', got '{proposal.status}'"
        )
    finally:
        await _cleanup(strategy_id)


@pytest.mark.asyncio
async def test_sweep_preserves_recent_pending():
    """A pending proposal only 1 day old must remain 'pending' after _tick."""
    strategy_id = f"TEST_sweep_{uuid.uuid4().hex[:6]}"
    prop_id = await _insert_proposal(strategy_id, age_days=1)
    try:
        from reflection.main import _tick
        await _tick(window_hours=24, use_llm=False, min_outcomes=10, score_trigger=-0.05)

        async with shared_session_scope() as session:
            proposal = await session.get(MutationProposal, prop_id)
        assert proposal is not None
        assert proposal.status == "pending", (
            f"Expected 'pending', got '{proposal.status}'"
        )
    finally:
        await _cleanup(strategy_id)
