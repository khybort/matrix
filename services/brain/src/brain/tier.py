"""DB-tier routing for the Brain's sql_read tool.

Matrix splits storage across two tiers (see matrix_shared.db): LOCAL holds
market data + raw documents + the AGE graph; SHARED holds predictions,
wallets, lessons, strategy configs, etc. In single-PC dev both point at the
same Postgres, but the Brain still routes each read to the correct pool so it
behaves in multi-PC setups too.
"""

from __future__ import annotations

from matrix_shared.agent_runtime.guards import referenced_tables

# Tables that live on the LOCAL tier. Everything else in the allow-list is SHARED.
LOCAL_TABLES = frozenset({"market_bars", "market_trades", "raw_documents"})


def choose_tier(sql: str, *, local_tables: frozenset[str] = LOCAL_TABLES) -> str:
    """Return 'local' if the query touches any LOCAL-tier table, else 'shared'."""
    if referenced_tables(sql) & local_tables:
        return "local"
    return "shared"
