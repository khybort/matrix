"""Reasoning-episode overlay writers usable from ANY service.

`services/graph/overlay.py` defines the Prediction/Outcome/Lesson/Strategy
overlay on the AGE graph, but only the agent could import it — so only
Prediction nodes were ever written and "what did we decide → what happened →
what did we learn" returned dead ends (docs/AUTONOMY_PLAN.md §4.1). This
module carries the Outcome and Lesson writers with their own AGE prelude so
the paper engine and the lessons synthesizer can complete the chain:

    (:Strategy)-[:PRODUCED]->(:Prediction)-[:RESULTED_IN]->(:Outcome)
    (:Outcome)-[:GENERALIZED_INTO]->(:Lesson)   (lesson ← its bucket's outcomes)

Best-effort by contract: every executor swallows and logs; a graph failure
never touches the relational write that is the source of truth.
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from sqlalchemy import text

from matrix_shared.db import local_session_scope

_GRAPH = "matrix_graph"


def _cypher_str(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace("'", "\\'")


def _cypher(body: str, ret: str = "x") -> str:
    return f"SELECT * FROM cypher('{_GRAPH}', $$ {body} $$) AS ({ret} agtype)"


async def _exec(session, cypher_select: str) -> Any:
    await session.execute(text("LOAD 'age'"))
    await session.execute(text("SET search_path = ag_catalog, public"))
    return await session.execute(text(cypher_select))


# --------------------------------------------------------------- builders

def outcome_upsert_cypher(*, outcome_id: str, score: str, pnl_usd: str, reason: str) -> str:
    body = (
        f"MERGE (o:Outcome {{id: '{outcome_id}'}}) "
        f"SET o.score = {score}, o.pnl_usd = {pnl_usd}, o.reason = '{_cypher_str(reason)}' RETURN o"
    )
    return _cypher(body, "o")


def prediction_resulted_in_outcome_cypher(*, pred_id: str, outcome_id: str, score: str) -> str:
    body = (
        f"MATCH (p:Prediction {{id: '{pred_id}'}}), (o:Outcome {{id: '{outcome_id}'}}) "
        f"MERGE (p)-[r:RESULTED_IN]->(o) SET r.score = {score} RETURN r"
    )
    return _cypher(body, "r")


def lesson_upsert_cypher(
    *, lesson_id: str, pattern_kind: str, verdict: str, confidence: str, description: str,
    status: str = "active", symbol: str | None = None, strategy_id: str | None = None,
) -> str:
    extra = ""
    if symbol:
        extra += f", l.symbol = '{_cypher_str(symbol)}'"
    if strategy_id:
        extra += f", l.strategy_id = '{_cypher_str(strategy_id)}'"
    body = (
        f"MERGE (l:Lesson {{id: '{lesson_id}'}}) "
        f"SET l.pattern_kind = '{_cypher_str(pattern_kind)}', l.verdict = '{_cypher_str(verdict)}', "
        f"l.confidence = {confidence}, l.description = '{_cypher_str(description)}', "
        f"l.status = '{_cypher_str(status)}'{extra} RETURN l"
    )
    return _cypher(body, "l")


def lesson_status_cypher(*, lesson_id: str, status: str) -> str:
    body = f"MATCH (l:Lesson {{id: '{lesson_id}'}}) SET l.status = '{_cypher_str(status)}' RETURN l"
    return _cypher(body, "l")


def outcomes_generalized_into_lesson_cypher(*, lesson_id: str, symbol: str, side: str, strategy_id: str) -> str:
    """Link every Outcome of matching Predictions (same strategy/symbol/side) to the lesson."""
    body = (
        f"MATCH (p:Prediction {{symbol: '{_cypher_str(symbol)}', side: '{_cypher_str(side)}', "
        f"strategy_id: '{_cypher_str(strategy_id)}'}})-[ri:RESULTED_IN]->(o:Outcome), "
        f"(l:Lesson {{id: '{lesson_id}'}}) "
        f"MERGE (o)-[r:GENERALIZED_INTO]->(l) RETURN count(r)"
    )
    return _cypher(body, "n")


# --------------------------------------------------------------- executors

async def link_outcome_node(*, pred_id: str, outcome_id: str, score: str, pnl_usd: str, reason: str) -> bool:
    try:
        async with local_session_scope() as session:
            await _exec(session, outcome_upsert_cypher(outcome_id=outcome_id, score=score, pnl_usd=pnl_usd, reason=reason))
            await _exec(session, prediction_resulted_in_outcome_cypher(pred_id=pred_id, outcome_id=outcome_id, score=score))
        return True
    except Exception as e:  # noqa: BLE001
        logger.debug(f"graph overlay: outcome link skipped ({e})")
        return False


async def upsert_lesson_node(
    *, lesson_id: str, pattern_kind: str, verdict: str, confidence: str, description: str,
    status: str = "active", symbol: str | None = None, side: str | None = None, strategy_id: str | None = None,
) -> bool:
    try:
        async with local_session_scope() as session:
            await _exec(session, lesson_upsert_cypher(
                lesson_id=lesson_id, pattern_kind=pattern_kind, verdict=verdict, confidence=confidence,
                description=description, status=status, symbol=symbol, strategy_id=strategy_id,
            ))
            if symbol and side and strategy_id:
                await _exec(session, outcomes_generalized_into_lesson_cypher(
                    lesson_id=lesson_id, symbol=symbol, side=side, strategy_id=strategy_id))
        return True
    except Exception as e:  # noqa: BLE001
        logger.debug(f"graph overlay: lesson upsert skipped ({e})")
        return False


async def set_lesson_status(*, lesson_id: str, status: str) -> bool:
    try:
        async with local_session_scope() as session:
            await _exec(session, lesson_status_cypher(lesson_id=lesson_id, status=status))
        return True
    except Exception as e:  # noqa: BLE001
        logger.debug(f"graph overlay: lesson status skipped ({e})")
        return False
