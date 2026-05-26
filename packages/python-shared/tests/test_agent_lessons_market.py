"""Phase E parity assertions for agent_lessons.

DB-free: inspects the AgentLesson ORM and the active_lessons signature.
End-to-end synthesizer behaviour is exercised by the agent_lessons
service integration tests (which run against Postgres).
"""

from __future__ import annotations

import inspect

from matrix_shared.agent_lessons import active_lessons
from matrix_shared.models import AgentLesson


def test_agent_lesson_has_asset_class_column() -> None:
    cols = {c.name: c for c in AgentLesson.__table__.columns}
    assert "asset_class" in cols, "AgentLesson.asset_class column missing"
    col = cols["asset_class"]
    assert not col.nullable
    assert col.default is not None and col.default.arg == "crypto"


def test_per_market_status_index_present() -> None:
    indexes = {ix.name: ix for ix in AgentLesson.__table__.indexes}
    ix = indexes.get("ix_agent_lessons_strategy_market_status")
    assert ix is not None, "missing ix_agent_lessons_strategy_market_status"
    col_names = [c.name for c in ix.columns]
    assert col_names == ["strategy_id", "asset_class", "status"]


def test_active_lessons_accepts_asset_class_kwarg() -> None:
    sig = inspect.signature(active_lessons)
    assert "asset_class" in sig.parameters
    param = sig.parameters["asset_class"]
    assert param.default is None, (
        "asset_class default must be None — diagnostic callers want the "
        "full set, decision-time callers must opt in by passing a market"
    )
