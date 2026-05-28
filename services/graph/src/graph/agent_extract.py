"""Per-document entity/relation extraction via the shared SDK tool loop.

Same shape as the synthesis agent: read-only tools so the model aligns its
canonicals to entities already in the graph (avoids `BTC` vs `Bitcoin` style
drift), then emits the final JSON. Writes (graph upserts) stay in the
service code outside the loop.
"""

from __future__ import annotations

import orjson
from loguru import logger
from matrix_shared import local_session_scope
from matrix_shared.agent_runtime.ratelimit import get_rate_limiter
from matrix_shared.agent_runtime.tool import (
    ToolRegistry,
    build_sdk_mcp_server,
    mcp_tool_names,
    tool,
)
from matrix_shared.subscription_llm import call_subscription_agent, subscription_enabled

from graph.age import _exec_cypher
from graph.parsing import (
    ALLOWED_EDGE_TYPES,
    ALLOWED_ENTITY_TYPES,
    Entity,
    Relation,
    extract_json_object,
    parse_extraction,
)

# Per-doc body cap in the prompt. 1800 char ≈ 450 token covers headline + lede
# + 2-3 paragraphs, which is where entities live; the tail rarely adds new ones.
BODY_CHARS = 1800
MAX_TURNS = 6
_SERVER = "graph_extract"


def _text(payload: object) -> dict:
    return {"content": [{"type": "text", "text": orjson.dumps(payload, default=str).decode()}]}


def _error(msg: str) -> dict:
    return {"content": [{"type": "text", "text": f"ERROR: {msg}"}], "is_error": True}


async def _list_label(label: str, prop: str = "canonical", limit: int = 200) -> list[str]:
    cypher = (
        f"SELECT * FROM cypher('matrix_graph', $$ "
        f"MATCH (n:{label}) RETURN n.{prop} LIMIT {limit} $$) AS (n agtype)"
    )
    async with local_session_scope() as session:
        result = await _exec_cypher(session, cypher)
        return [str(r[0]) for r in result.fetchall()]


def _build_registry() -> ToolRegistry:
    reg = ToolRegistry()

    @tool(
        "existing_assets",
        "Asset canonicals already in the graph (BTC, ETH, ...). Align extracted "
        "Asset canonicals to these where possible so the same asset always merges.",
        {},
    )
    async def existing_assets(_args: dict) -> dict:
        try:
            return _text(await _list_label("Asset"))
        except Exception as e:
            return _error(f"cypher failed: {e}")

    @tool(
        "existing_companies",
        "Company canonicals already in the graph. Align extracted Company "
        "canonicals to these to avoid 'Binance Holdings' vs 'Binance' splits.",
        {},
    )
    async def existing_companies(_args: dict) -> dict:
        try:
            return _text(await _list_label("Company"))
        except Exception as e:
            return _error(f"cypher failed: {e}")

    for t in (existing_assets, existing_companies):
        reg.add(t)
    return reg


# Module-level literal — byte-stable across processes (ALLOWED_*_TYPES are
# constant tuples with deterministic iteration order). Stable system_prompt
# is the only handle we have at our layer toward CLI-side prompt caching.
_ENTITY_LIST = "|".join(ALLOWED_ENTITY_TYPES)
_EDGE_LIST = ", ".join(ALLOWED_EDGE_TYPES)
SYSTEM_PROMPT = (
    "You are a graph-extraction agent for financial news. "
    "First, optionally call `existing_assets` / `existing_companies` so your "
    "canonicals align with what is already in the graph. Then extract typed "
    "entities and inter-entity relations from the article.\n"
    f"Allowed entity types: {_ENTITY_LIST}.\n"
    f"Allowed edge types: {_EDGE_LIST}.\n"
    "Use canonical tickers for Asset (BTC, ETH, ...), legal names for Company, "
    "full names for Person. Events are categories (e.g. 'ETF approval'); "
    "Concepts are abstract themes. Emit a relation only when the article "
    "clearly supports it. Max 12 entities and 12 relations.\n"
    "Your tools are read-only. End your reply with ONLY a JSON object:\n"
    '{"entities":[{"type":"Asset","canonical":"BTC","display":"Bitcoin"}],'
    '"relations":[{"source":{"type":"Company","canonical":"BlackRock"},'
    '"edge":"OWNS","target":{"type":"Asset","canonical":"BTC"}}]}'
)


async def run_extract_agent(
    title: str | None, body: str | None
) -> tuple[list[Entity], list[Relation]] | None:
    """Drive the tool loop for one document. Returns None to signal fallback."""
    if not subscription_enabled():
        return None
    registry = _build_registry()
    server = build_sdk_mcp_server(_SERVER, registry)
    allowed = mcp_tool_names(_SERVER, registry)
    allowed_set = frozenset(allowed)

    def _deny(name: str, _params: dict) -> bool:
        return name in allowed_set

    user = f"TITLE: {title or ''}\n\nBODY: {(body or '')[:BODY_CHARS]}"
    parts: list[str] = []
    try:
        async for ev in call_subscription_agent(
            prompt=user,
            system=SYSTEM_PROMPT,
            mcp_servers={_SERVER: server},
            allowed_tools=allowed,
            disallowed_tools=["Bash", "Write", "Edit", "NotebookEdit"],
            can_use_tool=_deny,
            max_turns=MAX_TURNS,
            session_id=_SERVER,
            limiter=get_rate_limiter(),
        ):
            if ev.type == "assistant_text":
                parts.append(ev.payload.get("text", ""))
    except Exception as e:
        logger.warning(f"graph extract agent loop failed: {e}")
        return None

    text = "".join(parts).strip()
    if not text:
        return None
    entities, relations = parse_extraction(extract_json_object(text))
    return (entities, relations) if entities else None
