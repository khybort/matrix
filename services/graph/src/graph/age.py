"""Apache AGE Cypher helpers — node + edge upsert into matrix_graph.

AGE in Postgres requires:
    LOAD 'age';
    SET search_path = ag_catalog, public;

We send these on every transaction since SQLAlchemy pools may rotate connections.
Cypher queries return `agtype` cells; we wrap them as text and parse the
small portion we need.

For now this module exposes:
    upsert_document(doc_id, source, title, url, published_ts)
    upsert_entity(entity_type, canonical, display, props)
    link_mentions(doc_id, entity_type, entity_canonical)

All MERGE-based so re-runs are safe.
"""

from __future__ import annotations

import uuid
from typing import Any

from loguru import logger
from sqlalchemy import text

from matrix_shared import session_scope

def _cypher_str(s: str) -> str:
    """Escape a string for inclusion in a Cypher literal."""
    return s.replace("\\", "\\\\").replace("'", "\\'")


async def _exec_cypher(session, cypher_select: str) -> Any:
    """Execute a Cypher query, prepending the AGE prelude statements separately.

    asyncpg doesn't allow multiple statements in a single prepared statement,
    so LOAD/SET and the cypher SELECT must be sent in separate execute calls.
    """
    await session.execute(text("LOAD 'age'"))
    await session.execute(text("SET search_path = ag_catalog, public"))
    return await session.execute(text(cypher_select))


async def upsert_document(
    doc_id: uuid.UUID,
    source: str,
    title: str | None,
    url: str | None,
    published_iso: str | None,
) -> None:
    title_s = _cypher_str(title or "")
    url_s = _cypher_str(url or "")
    src_s = _cypher_str(source)
    pub_s = _cypher_str(published_iso or "")
    cypher = (
        f"SELECT * FROM cypher('matrix_graph', $$ "
        f"MERGE (d:Document {{id: '{doc_id}'}}) "
        f"SET d.source = '{src_s}', d.title = '{title_s}', d.url = '{url_s}', "
        f"d.published_at = '{pub_s}' "
        f"RETURN d "
        f"$$) AS (d agtype)"
    )
    async with session_scope() as session:
        await _exec_cypher(session, cypher)


async def upsert_entity(
    entity_type: str, canonical: str, display: str, props: dict[str, Any] | None = None
) -> None:
    can_s = _cypher_str(canonical)
    disp_s = _cypher_str(display)
    label = "".join(c for c in entity_type if c.isalpha()) or "Entity"
    cypher = (
        f"SELECT * FROM cypher('matrix_graph', $$ "
        f"MERGE (e:{label} {{canonical: '{can_s}'}}) "
        f"SET e.display = '{disp_s}' "
        f"RETURN e "
        f"$$) AS (e agtype)"
    )
    async with session_scope() as session:
        await _exec_cypher(session, cypher)


async def link_mentions(
    doc_id: uuid.UUID, entity_type: str, entity_canonical: str
) -> None:
    label = "".join(c for c in entity_type if c.isalpha()) or "Entity"
    can_s = _cypher_str(entity_canonical)
    cypher = (
        f"SELECT * FROM cypher('matrix_graph', $$ "
        f"MATCH (d:Document {{id: '{doc_id}'}}), (e:{label} {{canonical: '{can_s}'}}) "
        f"MERGE (d)-[r:MENTIONS]->(e) "
        f"RETURN r "
        f"$$) AS (r agtype)"
    )
    async with session_scope() as session:
        await _exec_cypher(session, cypher)


async def link_typed_edge(
    source_type: str,
    source_canonical: str,
    edge_type: str,
    target_type: str,
    target_canonical: str,
    *,
    source_doc_id: uuid.UUID | None = None,
) -> None:
    """MERGE a typed relationship between two existing entities.

    Optional source_doc_id stamps the edge with provenance — handy when we
    want to know WHICH document supported this claim. Stored as a property
    on the edge so multiple supporting documents accumulate naturally.
    """
    src_label = "".join(c for c in source_type if c.isalpha()) or "Entity"
    tgt_label = "".join(c for c in target_type if c.isalpha()) or "Entity"
    edge_label = "".join(c for c in edge_type if c.isalnum() or c == "_").upper() or "RELATED_TO"
    src_can = _cypher_str(source_canonical)
    tgt_can = _cypher_str(target_canonical)

    if source_doc_id is None:
        cypher = (
            f"SELECT * FROM cypher('matrix_graph', $$ "
            f"MATCH (s:{src_label} {{canonical: '{src_can}'}}), "
            f"      (t:{tgt_label} {{canonical: '{tgt_can}'}}) "
            f"MERGE (s)-[r:{edge_label}]->(t) "
            f"RETURN r "
            f"$$) AS (r agtype)"
        )
    else:
        cypher = (
            f"SELECT * FROM cypher('matrix_graph', $$ "
            f"MATCH (s:{src_label} {{canonical: '{src_can}'}}), "
            f"      (t:{tgt_label} {{canonical: '{tgt_can}'}}) "
            f"MERGE (s)-[r:{edge_label}]->(t) "
            f"ON CREATE SET r.first_seen = '{source_doc_id}', r.support_count = 1 "
            f"ON MATCH SET r.support_count = coalesce(r.support_count, 0) + 1, "
            f"             r.last_seen = '{source_doc_id}' "
            f"RETURN r "
            f"$$) AS (r agtype)"
        )
    async with session_scope() as session:
        await _exec_cypher(session, cypher)


async def graph_summary() -> dict[str, int]:
    """Quick counts of node labels + edge count, for smoketest."""
    queries = {
        "Document": "MATCH (d:Document) RETURN count(d)",
        "Asset": "MATCH (a:Asset) RETURN count(a)",
        "Company": "MATCH (c:Company) RETURN count(c)",
        "Person": "MATCH (p:Person) RETURN count(p)",
        "Event": "MATCH (e:Event) RETURN count(e)",
        "Concept": "MATCH (c:Concept) RETURN count(c)",
        "MENTIONS": "MATCH ()-[r:MENTIONS]->() RETURN count(r)",
    }
    out: dict[str, int] = {}
    for label, cypher_q in queries.items():
        full = f"SELECT * FROM cypher('matrix_graph', $$ {cypher_q} $$) AS (n agtype)"
        async with session_scope() as session:
            try:
                result = await _exec_cypher(session, full)
                row = result.first()
                if row is None:
                    out[label] = 0
                    continue
                raw = str(row[0])
                out[label] = int(raw.strip().strip('"'))
            except Exception as e:
                logger.warning(f"graph_summary {label}: {e}")
                out[label] = -1
    return out
