"""Read-only guards for the agent tool belt (SQL + Cypher).

DB-free unit tests. These guards are defense-in-depth on top of the
DB-enforced `SET TRANSACTION READ ONLY`; they reject obvious writes and
constrain reads to an allow-listed set of tables before a query ever
reaches Postgres / AGE.
"""

from __future__ import annotations

import pytest

from matrix_shared.agent_runtime.guards import (
    UnsafeQueryError,
    ensure_read_only_cypher,
    ensure_read_only_sql,
    referenced_tables,
)

ALLOWED = {"predictions", "outcomes", "wallets"}


# ---- SQL ----

def test_select_passes_and_gets_default_limit():
    out = ensure_read_only_sql("SELECT * FROM predictions", ALLOWED)
    assert "limit 500" in out.lower()


def test_existing_limit_is_preserved():
    out = ensure_read_only_sql("SELECT * FROM predictions LIMIT 10", ALLOWED)
    assert out.lower().count("limit") == 1
    assert "10" in out


def test_trailing_semicolon_is_tolerated():
    out = ensure_read_only_sql("SELECT * FROM predictions;", ALLOWED)
    assert "limit" in out.lower()


def test_with_cte_passes_and_cte_name_is_not_a_table_violation():
    sql = "WITH recent AS (SELECT * FROM predictions) SELECT * FROM recent"
    out = ensure_read_only_sql(sql, ALLOWED)
    assert "limit" in out.lower()


def test_join_target_is_allow_list_checked():
    out = ensure_read_only_sql(
        "SELECT * FROM predictions p JOIN outcomes o ON o.prediction_id = p.id",
        ALLOWED,
    )
    assert "limit" in out.lower()


def test_insert_is_rejected():
    with pytest.raises(UnsafeQueryError):
        ensure_read_only_sql("INSERT INTO predictions VALUES (1)", ALLOWED)


def test_update_is_rejected():
    with pytest.raises(UnsafeQueryError):
        ensure_read_only_sql("UPDATE predictions SET confidence = 1", ALLOWED)


def test_stacked_delete_after_select_is_rejected():
    with pytest.raises(UnsafeQueryError):
        ensure_read_only_sql("SELECT 1; DELETE FROM predictions", ALLOWED)


def test_two_select_statements_rejected():
    with pytest.raises(UnsafeQueryError):
        ensure_read_only_sql(
            "SELECT * FROM predictions; SELECT * FROM outcomes", ALLOWED
        )


def test_non_allow_listed_table_is_rejected():
    with pytest.raises(UnsafeQueryError):
        ensure_read_only_sql("SELECT * FROM paper_trade_certificate", ALLOWED)


def test_column_named_like_a_keyword_is_not_a_false_positive():
    # created_at / updated_at must not trip the create/update keyword guard.
    out = ensure_read_only_sql(
        "SELECT created_at, updated_at FROM predictions", ALLOWED
    )
    assert "limit" in out.lower()


# ---- referenced_tables ----

def test_referenced_tables_extracts_from_and_join():
    assert referenced_tables(
        "SELECT * FROM predictions p JOIN outcomes o ON o.prediction_id = p.id"
    ) == {"predictions", "outcomes"}


def test_referenced_tables_excludes_cte_names():
    assert referenced_tables(
        "WITH recent AS (SELECT * FROM predictions) SELECT * FROM recent"
    ) == {"predictions"}


def test_referenced_tables_strips_schema_qualifier():
    assert referenced_tables("SELECT * FROM public.wallets") == {"wallets"}


# ---- Cypher ----

def test_cypher_match_return_passes_unchanged():
    q = "MATCH (a:Asset) RETURN a"
    assert ensure_read_only_cypher(q) == q


def test_cypher_create_is_rejected():
    with pytest.raises(UnsafeQueryError):
        ensure_read_only_cypher("CREATE (a:Asset {canonical:'BTC'})")


def test_cypher_merge_is_rejected():
    with pytest.raises(UnsafeQueryError):
        ensure_read_only_cypher("MERGE (a:Asset {canonical:'BTC'}) RETURN a")


def test_cypher_set_is_rejected():
    with pytest.raises(UnsafeQueryError):
        ensure_read_only_cypher("MATCH (a:Asset) SET a.x = 1 RETURN a")


def test_cypher_detach_delete_is_rejected():
    with pytest.raises(UnsafeQueryError):
        ensure_read_only_cypher("MATCH (a) DETACH DELETE a")
