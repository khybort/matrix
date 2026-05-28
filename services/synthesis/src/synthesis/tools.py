"""Read-only tool belt for the synthesis agent.

The agent reads recent documents and the existing graph coverage (Concepts /
Assets) so it can produce non-duplicating, well-grounded themes. Writes are
done by the service AFTER the loop completes (see synthesize.py), keeping
the LLM strictly read-only over system state — same defense-in-depth pattern
as the Brain.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import orjson
from graph.age import _exec_cypher
from matrix_shared import local_session_scope
from matrix_shared.agent_runtime.tool import ToolRegistry, tool
from matrix_shared.models import RawDocument
from sqlalchemy import desc, select

MAX_DOCS = 60
TITLE_CHARS = 200
BODY_CHARS = 400


def _text(payload: Any) -> dict:
    return {"content": [{"type": "text", "text": orjson.dumps(payload, default=str).decode()}]}


def _error(message: str) -> dict:
    return {"content": [{"type": "text", "text": f"ERROR: {message}"}], "is_error": True}


def build_registry(*, window_hours: float) -> ToolRegistry:
    """Build the synthesis tool belt closing over the default lookback window."""
    reg = ToolRegistry()

    @tool(
        "recent_documents",
        "Return the last N hours of news/filings/transcripts (titles + body snippets). "
        "Defaults to the loop's configured window.",
        {"hours": float},
    )
    async def recent_documents(args: dict) -> dict:
        hours = float(args.get("hours", window_hours))
        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        async with local_session_scope() as session:
            stmt = (
                select(RawDocument)
                .where(RawDocument.published_at >= cutoff)
                .order_by(desc(RawDocument.published_at))
                .limit(MAX_DOCS)
            )
            docs = list((await session.execute(stmt)).scalars())
        out = [
            {
                "i": i,
                "source": d.source,
                "published_at": d.published_at.isoformat() if d.published_at else None,
                "title": (d.title or "")[:TITLE_CHARS],
                "body": (d.body or "")[:BODY_CHARS],
            }
            for i, d in enumerate(docs, 1)
        ]
        return _text(out)

    @tool(
        "existing_concepts",
        "List Concept nodes already in the knowledge graph — use this to avoid "
        "re-emitting themes that already exist with the same canonical.",
        {},
    )
    async def existing_concepts(_args: dict) -> dict:
        cypher = (
            "SELECT * FROM cypher('matrix_graph', $$ "
            "MATCH (c:Concept) RETURN c LIMIT 100 $$) AS (c agtype)"
        )
        try:
            async with local_session_scope() as session:
                result = await _exec_cypher(session, cypher)
                rows = [str(r[0]) for r in result.fetchall()]
        except Exception as e:
            return _error(f"cypher failed: {e}")
        return _text(rows)

    @tool(
        "existing_assets",
        "Asset canonicals already tracked in the graph — use these tickers when "
        "listing impacted_assets so themes link to existing Asset nodes.",
        {},
    )
    async def existing_assets(_args: dict) -> dict:
        cypher = (
            "SELECT * FROM cypher('matrix_graph', $$ "
            "MATCH (a:Asset) RETURN a.canonical LIMIT 200 $$) AS (n agtype)"
        )
        try:
            async with local_session_scope() as session:
                result = await _exec_cypher(session, cypher)
                rows = [str(r[0]) for r in result.fetchall()]
        except Exception as e:
            return _error(f"cypher failed: {e}")
        return _text(rows)

    for t in (recent_documents, existing_concepts, existing_assets):
        reg.add(t)
    return reg
