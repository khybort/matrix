"""Read-only guards for agent tool belts.

These are *defense in depth*. The authoritative read-only guarantee is the
DB-side `SET TRANSACTION READ ONLY` block the tool runs inside (a write
raises at Postgres). These guards reject obvious writes earlier and confine
SQL reads to an allow-listed set of tables, so a malformed or adversarial
model request never reaches the database in the first place.

Neither guard is a full SQL/Cypher parser — they pattern-match the common,
high-signal cases. Anything subtle is still caught by the read-only
transaction and (for the brain) the absence of any write tool.
"""

from __future__ import annotations

import re

# SQL statement keywords that mutate state or escalate privileges. Matched as
# whole words, case-insensitive — so `created_at` / `updated_at` are safe.
_SQL_FORBIDDEN = (
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "create",
    "truncate",
    "grant",
    "revoke",
    "merge",
    "copy",
    "call",
    "do",
    "vacuum",
    "comment",
)

# Cypher clauses that write to the graph.
_CYPHER_FORBIDDEN = ("create", "merge", "set", "delete", "remove", "detach", "drop")

_TABLE_REF = re.compile(r"\b(?:from|join)\s+([a-zA-Z_][\w.]*)", re.IGNORECASE)
_CTE_DEF = re.compile(r"\b(?:with|,)\s+([a-zA-Z_]\w*)\s+as\s*\(", re.IGNORECASE)
_LIMIT = re.compile(r"\blimit\b", re.IGNORECASE)


class UnsafeQueryError(ValueError):
    """Raised when a query is rejected by a read-only guard."""


def _has_word(haystack: str, word: str) -> bool:
    return re.search(rf"\b{re.escape(word)}\b", haystack, re.IGNORECASE) is not None


def ensure_read_only_sql(
    sql: str,
    allowed_tables: set[str],
    *,
    default_limit: int = 500,
) -> str:
    """Validate `sql` is a single read-only statement over allow-listed tables.

    Returns the (possibly LIMIT-augmented) SQL. Raises UnsafeQueryError on any
    rejection.
    """
    stripped = sql.strip()
    if not stripped:
        raise UnsafeQueryError("empty query")

    # Strip a single trailing semicolon; any remaining one means statement
    # stacking (e.g. `SELECT 1; DELETE ...`), which we never allow.
    body = stripped.rstrip()
    if body.endswith(";"):
        body = body[:-1].rstrip()
    if ";" in body:
        raise UnsafeQueryError("multiple statements are not allowed")

    head = body.lstrip().split(None, 1)[0].lower()
    if head not in ("select", "with"):
        raise UnsafeQueryError("only SELECT / WITH queries are allowed")

    for kw in _SQL_FORBIDDEN:
        if _has_word(body, kw):
            raise UnsafeQueryError(f"forbidden keyword: {kw}")

    cte_names = {m.group(1).lower() for m in _CTE_DEF.finditer(body)}
    for m in _TABLE_REF.finditer(body):
        table = m.group(1).split(".")[-1].strip('"').lower()
        if table in cte_names:
            continue
        if table not in allowed_tables:
            raise UnsafeQueryError(f"table not in allow-list: {table}")

    if not _LIMIT.search(body):
        body = f"{body} LIMIT {default_limit}"
    return body


def ensure_read_only_cypher(query: str) -> str:
    """Validate a Cypher query contains no write clauses. Returns it unchanged."""
    stripped = query.strip()
    if not stripped:
        raise UnsafeQueryError("empty query")
    for kw in _CYPHER_FORBIDDEN:
        if _has_word(stripped, kw):
            raise UnsafeQueryError(f"forbidden cypher clause: {kw.upper()}")
    return query
