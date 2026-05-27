"""Context-graph overlay: reasoning episodes as nodes over the AGE graph.

The AGE graph already models the *domain* (Document/Asset/Company/...). This
overlay layers the system's own reasoning — Prediction / Decision / Outcome /
Lesson / Strategy — onto it so agents (and the Brain's cypher_query) can
traverse "what did we decide, what happened, what did we learn" in one graph.

Design rule (see docs plan): the graph is an INDEX over the ACID relational
rows, not the source of truth. Node keys reuse the relational PKs; money and
risk numbers stay authoritative in Postgres. All writes are idempotent MERGEs
and best-effort — a failed graph write never breaks the relational write.

This module is split into pure Cypher *builders* (unit-tested, no DB) and thin
async *executors* that run them via the same AGE prelude as age.py.
"""

from __future__ import annotations

from matrix_shared import session_scope

from graph.age import _cypher_str, _exec_cypher

_GRAPH = "matrix_graph"


def _cypher(body: str, ret: str = "x") -> str:
    return f"SELECT * FROM cypher('{_GRAPH}', $$ {body} $$) AS ({ret} agtype)"


def _bool(v: bool) -> str:
    return "true" if v else "false"


# --------------------------------------------------------------- node builders


def prediction_upsert_cypher(
    *,
    pred_id: str,
    symbol: str,
    side: str,
    confidence: str,
    strategy_id: str,
    asset_class: str,
    is_exploration: bool,
) -> str:
    body = (
        f"MERGE (p:Prediction {{id: '{pred_id}'}}) "
        f"SET p.symbol = '{_cypher_str(symbol)}', "
        f"p.side = '{_cypher_str(side)}', "
        f"p.confidence = {confidence}, "
        f"p.strategy_id = '{_cypher_str(strategy_id)}', "
        f"p.asset_class = '{_cypher_str(asset_class)}', "
        f"p.is_exploration = {_bool(is_exploration)} "
        f"RETURN p"
    )
    return _cypher(body, "p")


def outcome_upsert_cypher(
    *, outcome_id: str, score: str, pnl_usd: str, reason: str
) -> str:
    body = (
        f"MERGE (o:Outcome {{id: '{outcome_id}'}}) "
        f"SET o.score = {score}, o.pnl_usd = {pnl_usd}, "
        f"o.reason = '{_cypher_str(reason)}' "
        f"RETURN o"
    )
    return _cypher(body, "o")


def lesson_upsert_cypher(
    *, lesson_id: str, pattern_kind: str, verdict: str, confidence: str, description: str
) -> str:
    body = (
        f"MERGE (l:Lesson {{id: '{lesson_id}'}}) "
        f"SET l.pattern_kind = '{_cypher_str(pattern_kind)}', "
        f"l.verdict = '{_cypher_str(verdict)}', "
        f"l.confidence = {confidence}, "
        f"l.description = '{_cypher_str(description)}' "
        f"RETURN l"
    )
    return _cypher(body, "l")


def strategy_upsert_cypher(
    *, strategy_key: str, strategy_id: str, version: int, status: str
) -> str:
    body = (
        f"MERGE (s:Strategy {{key: '{_cypher_str(strategy_key)}'}}) "
        f"SET s.strategy_id = '{_cypher_str(strategy_id)}', "
        f"s.version = {version}, s.status = '{_cypher_str(status)}' "
        f"RETURN s"
    )
    return _cypher(body, "s")


# --------------------------------------------------------------- edge builders


def strategy_produced_prediction_cypher(*, strategy_key: str, pred_id: str) -> str:
    body = (
        f"MATCH (s:Strategy {{key: '{_cypher_str(strategy_key)}'}}), "
        f"(p:Prediction {{id: '{pred_id}'}}) "
        f"MERGE (s)-[r:PRODUCED]->(p) RETURN r"
    )
    return _cypher(body, "r")


def prediction_resulted_in_outcome_cypher(
    *, pred_id: str, outcome_id: str, score: str
) -> str:
    body = (
        f"MATCH (p:Prediction {{id: '{pred_id}'}}), "
        f"(o:Outcome {{id: '{outcome_id}'}}) "
        f"MERGE (p)-[r:RESULTED_IN]->(o) SET r.score = {score} RETURN r"
    )
    return _cypher(body, "r")


def outcome_generalized_into_lesson_cypher(*, outcome_id: str, lesson_id: str) -> str:
    body = (
        f"MATCH (o:Outcome {{id: '{outcome_id}'}}), "
        f"(l:Lesson {{id: '{lesson_id}'}}) "
        f"MERGE (o)-[r:GENERALIZED_INTO]->(l) RETURN r"
    )
    return _cypher(body, "r")


def prediction_predicts_asset_cypher(*, pred_id: str, asset_canonical: str) -> str:
    body = (
        f"MATCH (p:Prediction {{id: '{pred_id}'}}) "
        f"MERGE (a:Asset {{canonical: '{_cypher_str(asset_canonical)}'}}) "
        f"MERGE (p)-[r:PREDICTS]->(a) RETURN r"
    )
    return _cypher(body, "r")


# --------------------------------------------------------------- executors
# Best-effort: callers wrap in try/except; a graph-write failure must never
# break the relational write that is the source of truth.


async def upsert_prediction_node(
    *,
    pred_id: str,
    symbol: str,
    side: str,
    confidence: str,
    strategy_id: str,
    asset_class: str,
    is_exploration: bool,
    asset_canonical: str | None = None,
    strategy_key: str | None = None,
) -> None:
    """Upsert a Prediction node + its PRODUCED / PREDICTS edges (best-effort)."""
    async with session_scope() as session:
        await _exec_cypher(
            session,
            prediction_upsert_cypher(
                pred_id=pred_id, symbol=symbol, side=side, confidence=confidence,
                strategy_id=strategy_id, asset_class=asset_class,
                is_exploration=is_exploration,
            ),
        )
        if strategy_key:
            await _exec_cypher(session, strategy_upsert_cypher(
                strategy_key=strategy_key, strategy_id=strategy_id,
                version=int(strategy_key.rsplit(":", 1)[-1] or 0), status="active",
            ))
            await _exec_cypher(session, strategy_produced_prediction_cypher(
                strategy_key=strategy_key, pred_id=pred_id))
        if asset_canonical:
            await _exec_cypher(session, prediction_predicts_asset_cypher(
                pred_id=pred_id, asset_canonical=asset_canonical))


async def link_outcome_node(
    *, pred_id: str, outcome_id: str, score: str, pnl_usd: str, reason: str
) -> None:
    """Upsert an Outcome node and the Prediction-RESULTED_IN->Outcome edge."""
    async with session_scope() as session:
        await _exec_cypher(session, outcome_upsert_cypher(
            outcome_id=outcome_id, score=score, pnl_usd=pnl_usd, reason=reason))
        await _exec_cypher(session, prediction_resulted_in_outcome_cypher(
            pred_id=pred_id, outcome_id=outcome_id, score=score))
