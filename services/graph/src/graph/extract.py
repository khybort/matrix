"""Entity + relation extraction from raw_documents.

Three paths, tried in order until one succeeds:
    1. Agent tool loop (`graph.agent_extract.run_extract_agent`) — preferred.
       Reads existing graph Asset/Company canonicals so its output aligns to
       the entities already in the graph. Subscription-auth, multi-step.
    2. Legacy single-shot LLM (`llm_extract`) — fallback when subscription
       is unavailable or the agent path returns nothing.
    3. Heuristic keyword extractor (`heuristic_extract`) — final fallback;
       deterministic, no LLM, Asset + Company only.

Entity types emitted:
    Asset, Company, Person, Event, Concept (rich set via paths 1+2).

Validation / clamping is centralized in `graph.parsing.parse_extraction`.
`Entity` / `Relation` are re-exported here so existing imports keep working.
"""

from __future__ import annotations

import re

from loguru import logger
from matrix_shared import call_claude_json
from matrix_shared.subscription_llm import MODEL_SONNET

from graph.agent_extract import run_extract_agent
from graph.parsing import (
    ALLOWED_EDGE_TYPES,
    ALLOWED_ENTITY_TYPES,
    Entity,
    Relation,
    parse_extraction,
)

LLM_MODEL = MODEL_SONNET


# Canonical asset map: lowercase keyword → canonical ticker
ASSET_KEYWORDS: dict[str, str] = {
    "bitcoin": "BTC",
    "btc": "BTC",
    "ethereum": "ETH",
    "ether": "ETH",
    "eth": "ETH",
    "solana": "SOL",
    "sol": "SOL",
    "xrp": "XRP",
    "ripple": "XRP",
    "cardano": "ADA",
    "ada": "ADA",
    "dogecoin": "DOGE",
    "doge": "DOGE",
    "litecoin": "LTC",
    "ltc": "LTC",
    "tether": "USDT",
    "usdc": "USDC",
    "polygon": "MATIC",
    "matic": "MATIC",
    "avalanche": "AVAX",
    "avax": "AVAX",
    "polkadot": "DOT",
    "dot": "DOT",
}

COMPANY_KEYWORDS: dict[str, str] = {
    "binance": "Binance",
    "coinbase": "Coinbase",
    "kraken": "Kraken",
    "okx": "OKX",
    "bybit": "Bybit",
    "blackrock": "BlackRock",
    "fidelity": "Fidelity",
    "tether": "Tether",
    "circle": "Circle",
    "microstrategy": "MicroStrategy",
    "tesla": "Tesla",
    "grayscale": "Grayscale",
    "sec": "U.S. SEC",
    "cftc": "CFTC",
    "doj": "U.S. DOJ",
}


def heuristic_extract(title: str | None, body: str | None) -> list[Entity]:
    """Token-based extraction. Cheap, deterministic, no LLM."""
    text = " ".join(p for p in (title, body) if p).lower()
    if not text:
        return []
    tokens = set(re.findall(r"[a-z][a-z0-9]+", text))
    out: list[Entity] = []
    seen_canonical: set[tuple[str, str]] = set()

    for kw, canonical in ASSET_KEYWORDS.items():
        if kw in tokens and ("Asset", canonical) not in seen_canonical:
            out.append(Entity(type="Asset", canonical=canonical, display=canonical))
            seen_canonical.add(("Asset", canonical))

    for kw, name in COMPANY_KEYWORDS.items():
        if kw in tokens and ("Company", name) not in seen_canonical:
            out.append(Entity(type="Company", canonical=name, display=name))
            seen_canonical.add(("Company", name))

    return out


async def llm_extract(
    title: str | None, body: str | None
) -> tuple[list[Entity], list[Relation]] | None:
    """Single-shot legacy path. Builds the same JSON contract the agent emits."""
    user = f"TITLE: {title or ''}\n\nBODY: {(body or '')[:4000]}"
    edge_list = ", ".join(ALLOWED_EDGE_TYPES)
    entity_list = "|".join(ALLOWED_ENTITY_TYPES)
    system = (
        "Extract entities AND typed relations from this financial news article. "
        "Return ONLY a JSON object with two keys:\n"
        f'  "entities": [{{"type":"{entity_list}",'
        '"canonical":"<stable key>","display":"<readable>"}],\n'
        '  "relations": [{"source":{"type":"...","canonical":"..."},'
        f'"edge":"<one of {edge_list}>",'
        '"target":{"type":"...","canonical":"..."}}].\n'
        "Use canonical tickers for Asset (BTC, ETH, ...). Legal names for "
        "Company. Full names for Person. Events are categories like "
        "'ETF approval', 'security exploit'. Concepts are abstract themes "
        "like 'institutional adoption'.\n"
        "Only emit a relation when the article clearly supports it; do not "
        "speculate. Max 12 entities and 12 relations."
    )
    parsed = await call_claude_json(
        system=system, user=user, model=LLM_MODEL, max_tokens=1100, temperature=0.1,
    )
    if parsed is None:
        return None
    entities, relations = parse_extraction(parsed)
    if not entities:
        return None
    return entities, relations


async def extract_entities(
    title: str | None, body: str | None
) -> tuple[list[Entity], list[Relation], str]:
    """Top-level entry. Tries the agent tool loop, then single-shot LLM, then
    a keyword heuristic. Returns (entities, relations, source) where source is
    'agent' | 'llm' | 'heuristic'.
    """
    try:
        agent_result = await run_extract_agent(title, body)
    except Exception as e:
        logger.warning(f"extract: agent path raised: {e}")
        agent_result = None
    if agent_result is not None and agent_result[0]:
        return agent_result[0], agent_result[1], "agent"

    llm_result = await llm_extract(title, body)
    if llm_result is not None and llm_result[0]:
        return llm_result[0], llm_result[1], "llm"

    return heuristic_extract(title, body), [], "heuristic"


__all__ = [
    "ASSET_KEYWORDS",
    "COMPANY_KEYWORDS",
    "Entity",
    "Relation",
    "extract_entities",
    "heuristic_extract",
    "llm_extract",
]
