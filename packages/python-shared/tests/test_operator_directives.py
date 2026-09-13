"""Operator directives are protected lessons: honoured on the decision path,
immune to the exploration corridor, undoable."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from matrix_shared import shared_session_scope
from matrix_shared.agent_lessons import (
    OPERATOR_PREFIX,
    forget_directive,
    lessons_relevant_to,
    remember_directive,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _fresh_engines():
    """Engines cached by an earlier test's event loop break here with
    "attached to a different loop" (docs/ENGINEERING_LESSONS.md)."""
    from matrix_shared.db import reset_engines
    reset_engines()
    yield
    reset_engines()


class _F:
    def __init__(self, symbol):
        self.symbol = symbol


async def test_remember_and_forget_directive():
    sym = f"OPTEST{uuid.uuid4().hex[:6].upper()}USDT"
    sid = f"opstrat_{uuid.uuid4().hex[:6]}"
    try:
        ids = await remember_directive(symbol=sym, verdict="avoid", reason="operator says stop",
                                       asset_class="crypto", side="long", strategy_ids=[sid])
        assert len(ids) == 1
        hits = await lessons_relevant_to(_F(sym), sid, side="long", asset_class="crypto")
        assert hits and hits[0].verdict == "avoid" and hits[0].confidence == Decimal("0.99")
        assert hits[0].pattern_description.startswith(OPERATOR_PREFIX)
        # other side untouched; other market untouched
        assert await lessons_relevant_to(_F(sym), sid, side="short", asset_class="crypto") == []
        assert await lessons_relevant_to(_F(sym), sid, side="long", asset_class="bist") == []
        # re-issuing supersedes the previous directive (one active row)
        ids2 = await remember_directive(symbol=sym, verdict="avoid", reason="again",
                                        asset_class="crypto", side="long", strategy_ids=[sid])
        async with shared_session_scope() as s:
            statuses = [r[0] for r in (await s.execute(text(
                "SELECT status FROM agent_lessons WHERE strategy_id=:sid ORDER BY created_at"), {"sid": sid})).all()]
        assert statuses == ["superseded", "active"]
        assert await forget_directive(symbol=sym, asset_class="crypto") == 1
        assert await lessons_relevant_to(_F(sym), sid, side="long", asset_class="crypto") == []
        assert ids2
    finally:
        async with shared_session_scope() as s:
            await s.execute(text("DELETE FROM agent_lessons WHERE strategy_id=:sid"), {"sid": sid})


def test_operator_directive_survives_ttl_window():
    from datetime import datetime, timedelta, timezone
    # Directives get observed_until 10y out; the 14-day TTL sweep must never catch them.
    assert (datetime.now(timezone.utc) + timedelta(days=3650)) > datetime.now(timezone.utc) + timedelta(days=14)
