"""The Brain's read-only tool belt.

Every tool is registered with side_effect='read'; `build_registry` asserts the
whole belt is read-only before it is exposed to the model. SQL goes through
`ensure_read_only_sql` (SELECT/WITH only, allow-listed tables, auto-LIMIT) and
is tier-routed; Cypher goes through `ensure_read_only_cypher`.
"""

from __future__ import annotations

import orjson
from matrix_shared.agent_runtime.guards import (
    UnsafeQueryError,
    ensure_read_only_cypher,
    ensure_read_only_sql,
)
from matrix_shared.agent_runtime.tool import ToolRegistry, assert_all_read_only, tool

from brain.db import Pools
from brain.tier import LOCAL_TABLES, choose_tier

# Read allow-list spanning both tiers. Money/cert tables are readable (the
# Brain reports on them) but never writable — there is no write tool.
ALLOWED_TABLES: frozenset[str] = frozenset(
    {
        "predictions",
        "outcomes",
        "agent_lessons",
        "wallets",
        "wallet_snapshots",
        "paper_positions",
        "lab_experiments",
        "lab_evaluations",
        "strategy_configs",
        "mutation_proposals",
        "paper_trade_certificate",
        "market_bars",
        "market_trades",
        "raw_documents",
        "graph_signals",
        "bist_symbols",
    }
)

# Default summary cap. Tool result re-enters the model's context every loop
# turn, so smaller is cheaper. `verbose=true` raises it (the model has to
# explicitly opt in, making the cost decision visible in the trace).
_MAX_ROWS_DEFAULT = 25
_MAX_ROWS_VERBOSE = 100
# Per-field char cap before truncation. Brain answers usually want the
# *shape* of a row, not its blob fields (thesis text, json context, ...).
_DEFAULT_CHARS = 300
_VERBOSE_CHARS = 500


def _dumps(value: object) -> str:
    return orjson.dumps(value, default=str).decode()


def _text(payload: object) -> dict:
    return {"content": [{"type": "text", "text": _dumps(payload)}]}


def _error(message: str) -> dict:
    return {"content": [{"type": "text", "text": f"ERROR: {message}"}], "is_error": True}


def _truncate_row(row: dict, max_chars: int = _DEFAULT_CHARS) -> dict:
    """Cap each string value at `max_chars`. Non-strings pass through. Returns
    a new dict (caller-mutation-safe)."""
    out: dict = {}
    for k, v in row.items():
        if isinstance(v, str) and len(v) > max_chars:
            out[k] = v[: max_chars - 1] + "…"
        else:
            out[k] = v
    return out


def _summarize_rows(rows: list, *, head: int = 10, tail: int = 10) -> dict:
    """If rows ≤ head+tail return them whole; otherwise return head + tail +
    total + omitted so the model sees compressed but still useful shape."""
    total = len(rows)
    if total <= head + tail:
        return {"rows": rows, "total": total}
    return {
        "head": rows[:head],
        "tail": rows[-tail:],
        "total": total,
        "omitted": total - head - tail,
    }


def build_registry(pools: Pools) -> ToolRegistry:
    reg = ToolRegistry()

    @tool(
        "list_tables",
        "List the readable tables and which DB tier each lives on.",
        {},
    )
    async def list_tables(_args) -> dict:
        rows = [
            {"table": t, "tier": "local" if t in LOCAL_TABLES else "shared"}
            for t in sorted(ALLOWED_TABLES)
        ]
        return _text(rows)

    @tool(
        "describe_table",
        "Describe a table's columns. Arg: table (must be in the allow-list).",
        {"table": str},
    )
    async def describe_table(args) -> dict:
        table = str(args.get("table", "")).strip().lower()
        if table not in ALLOWED_TABLES:
            return _error(f"table not in allow-list: {table}")
        tier = "local" if table in LOCAL_TABLES else "shared"
        rows = await pools.for_tier(tier).fetch(
            """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = $1
            ORDER BY ordinal_position
            """,
            table,
        )
        return _text([dict(r) for r in rows])

    @tool(
        "sql_read",
        "Run a single read-only SELECT/WITH query over the allow-listed tables. "
        "Routed to the correct DB tier automatically. Default response is "
        "summarized (head 10 + tail 10 + total) with each string field capped "
        "to 300 chars. Pass verbose=true for the full payload (up to 100 rows, "
        "500-char fields) — only when you actually need the body content.",
        {"sql": str, "verbose": bool},
    )
    async def sql_read(args) -> dict:
        raw = str(args.get("sql", ""))
        verbose = bool(args.get("verbose", False))
        cap = _MAX_ROWS_VERBOSE if verbose else _MAX_ROWS_DEFAULT
        chars = _VERBOSE_CHARS if verbose else _DEFAULT_CHARS
        try:
            safe = ensure_read_only_sql(raw, set(ALLOWED_TABLES), default_limit=cap)
        except UnsafeQueryError as e:
            return _error(str(e))
        tier = choose_tier(safe)
        try:
            rows = await pools.for_tier(tier).fetch(safe)
        except Exception as e:  # surface DB errors to the model, don't crash the loop
            return _error(f"query failed: {e}")
        truncated = [_truncate_row(dict(r), max_chars=chars) for r in rows[:cap]]
        return _text(truncated if verbose else _summarize_rows(truncated))

    @tool(
        "cypher_query",
        "Run a read-only Cypher query against the AGE knowledge graph "
        "'matrix_graph' (nodes: Document/Asset/Company/Person/Event/Concept). "
        "RETURN a single column/map, e.g. MATCH (a:Asset) RETURN a LIMIT 20. "
        "Default response is summarized (head 10 + tail 10 + total). "
        "Pass verbose=true for the full list (up to 100 rows).",
        {"query": str, "verbose": bool},
    )
    async def cypher_query(args) -> dict:
        raw = str(args.get("query", ""))
        verbose = bool(args.get("verbose", False))
        cap = _MAX_ROWS_VERBOSE if verbose else _MAX_ROWS_DEFAULT
        try:
            safe = ensure_read_only_cypher(raw)
        except UnsafeQueryError as e:
            return _error(str(e))
        select = (
            f"SELECT * FROM cypher('matrix_graph', $$ {safe} $$) AS (result agtype)"
        )
        try:
            async with pools.local.acquire() as conn:
                await conn.execute("LOAD 'age'")
                await conn.execute("SET search_path = ag_catalog, public")
                rows = await conn.fetch(select)
        except Exception as e:
            return _error(f"cypher failed: {e}")
        out = [str(r["result"]) for r in rows[:cap]]
        return _text(out if verbose else _summarize_rows(out))

    for t in (list_tables, describe_table, sql_read, cypher_query):
        reg.add(t)
    assert_all_read_only(reg)
    return reg


__all__ = ["ALLOWED_TABLES", "build_registry"]
