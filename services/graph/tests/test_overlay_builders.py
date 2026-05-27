"""Pure Cypher-builder tests for the context-graph overlay.

The overlay layers the system's reasoning episodes (Decision / Prediction /
Outcome / Lesson / Strategy) onto the AGE knowledge graph as an index over the
ACID relational rows (graph keys reuse the relational PKs). These tests cover
only the Cypher *string* construction — execution against AGE is verified live.
"""

from __future__ import annotations

from graph.overlay import (
    prediction_predicts_asset_cypher,
    prediction_resulted_in_outcome_cypher,
    prediction_upsert_cypher,
    strategy_produced_prediction_cypher,
)

PID = "11111111-1111-1111-1111-111111111111"


def test_prediction_upsert_targets_graph_and_merges_by_id():
    c = prediction_upsert_cypher(
        pred_id=PID, symbol="BTCUSDT", side="long", confidence="0.42",
        strategy_id="matrix_agent", asset_class="crypto", is_exploration=False,
    )
    assert c.startswith("SELECT * FROM cypher('matrix_graph'")
    assert f"MERGE (p:Prediction {{id: '{PID}'}})" in c
    assert "p.side = 'long'" in c
    assert "p.confidence = 0.42" in c
    assert "p.is_exploration = false" in c
    assert "AS (p agtype)" in c


def test_prediction_upsert_marks_exploration_true():
    c = prediction_upsert_cypher(
        pred_id=PID, symbol="BTCUSDT", side="short", confidence="0.05",
        strategy_id="matrix_agent", asset_class="crypto", is_exploration=True,
    )
    assert "p.is_exploration = true" in c


def test_prediction_upsert_escapes_quotes():
    c = prediction_upsert_cypher(
        pred_id=PID, symbol="BT'C", side="long", confidence="0.1",
        strategy_id="x'y", asset_class="crypto", is_exploration=False,
    )
    # Single quotes must be backslash-escaped so the Cypher literal is valid.
    assert "BT\\'C" in c
    assert "x\\'y" in c


def test_strategy_produced_prediction_edge():
    c = strategy_produced_prediction_cypher(strategy_key="matrix_agent:3", pred_id=PID)
    assert "MERGE (s)-[r:PRODUCED]->(p)" in c
    assert "matrix_agent:3" in c
    assert PID in c


def test_prediction_resulted_in_outcome_edge():
    c = prediction_resulted_in_outcome_cypher(pred_id=PID, outcome_id="22", score="1.0")
    assert "MERGE (p)-[r:RESULTED_IN]->(o)" in c
    assert PID in c
    assert "'22'" in c


def test_prediction_predicts_asset_edge():
    c = prediction_predicts_asset_cypher(pred_id=PID, asset_canonical="BTC")
    assert "MERGE (p)-[r:PREDICTS]->(a)" in c
    assert "a:Asset {canonical: 'BTC'}" in c
