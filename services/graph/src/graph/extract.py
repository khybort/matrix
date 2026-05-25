"""Entity + relation extraction from raw_documents.

Default: keyword/heuristic-based extraction — works without LLM.
Optional: LLM mode via Vercel AI Gateway with structured JSON output.

Entity types emitted:
    Asset      — e.g. BTC, ETH, SOL (canonical ticker)
    Company    — e.g. Binance, Coinbase, BlackRock
    Person     — e.g. CEO/founder names mentioned alongside companies
    Event      — earnings/M&A/regulatory categories (when LLM is on)
    Concept    — e.g. "ETF approval", "fork", "halving"

For now we only emit Asset and Company in the heuristic path. The LLM path
returns the richer set.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from loguru import logger

from matrix_shared import call_claude_json

LLM_MODEL = "claude-haiku-4-5"


@dataclass(slots=True)
class Entity:
    type: str
    canonical: str  # canonical key — used for graph MERGE
    display: str
    props: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class Relation:
    """A typed edge between two extracted entities.

    The graph layer's MENTIONS edges (Document -> Entity) are still
    produced for every entity. Relations are *between* entities and
    encode the semantic link the LLM inferred from the document.
    """
    source_type: str
    source_canonical: str
    edge_type: str  # IMPACTS | EMPLOYS | ANNOUNCES | OWNS | REGULATES | RELATED_TO
    target_type: str
    target_canonical: str


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
    # Tokenize loosely
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


ALLOWED_ENTITY_TYPES = ("Asset", "Company", "Person", "Event", "Concept")
ALLOWED_EDGE_TYPES = (
    "IMPACTS",      # something affects an asset/company (most common)
    "EMPLOYS",      # Company → Person
    "ANNOUNCES",    # Company/Person → Event
    "OWNS",         # Company/Person → Asset/Company
    "REGULATES",    # Company (regulator) → Company/Asset
    "PARTNERS_WITH",
    "COMPETES_WITH",
    "PARTICIPATES_IN",  # Person/Company → Event
    "RELATED_TO",   # generic fallback
)


async def llm_extract(
    title: str | None, body: str | None
) -> tuple[list[Entity], list[Relation]] | None:
    """LLM-driven extraction; returns (entities, relations) or None on error.

    Relations are inter-entity edges (Company EMPLOYS Person, Event IMPACTS
    Asset, etc.). The Document→MENTIONS edges remain implicit and are written
    by the graph upsert layer for every emitted entity.
    """
    user = f"TITLE: {title or ''}\n\nBODY: {(body or '')[:4000]}"
    edge_list = ", ".join(ALLOWED_EDGE_TYPES)
    system = (
        "Extract entities AND typed relations from this financial news article. "
        "Return ONLY a JSON object with two keys:\n"
        '  "entities": [{"type":"Asset|Company|Person|Event|Concept",'
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

    # Entities
    raw_ents = parsed.get("entities") or []
    entities: list[Entity] = []
    seen_keys: set[tuple[str, str]] = set()
    for e in raw_ents[:12]:
        if not isinstance(e, dict):
            continue
        t = str(e.get("type", "")).strip()
        if t not in ALLOWED_ENTITY_TYPES:
            continue
        canonical = str(e.get("canonical", "")).strip()[:128]
        display = str(e.get("display", canonical))[:200]
        if not canonical:
            continue
        if (t, canonical) in seen_keys:
            continue
        seen_keys.add((t, canonical))
        entities.append(Entity(type=t, canonical=canonical, display=display))

    # Relations — both endpoints must be entities we actually emitted
    raw_rels = parsed.get("relations") or []
    relations: list[Relation] = []
    for r in raw_rels[:12]:
        if not isinstance(r, dict):
            continue
        edge = str(r.get("edge", "")).strip().upper()
        if edge not in ALLOWED_EDGE_TYPES:
            continue
        src = r.get("source") or {}
        tgt = r.get("target") or {}
        if not isinstance(src, dict) or not isinstance(tgt, dict):
            continue
        st = str(src.get("type", "")).strip()
        sc = str(src.get("canonical", "")).strip()[:128]
        tt = str(tgt.get("type", "")).strip()
        tc = str(tgt.get("canonical", "")).strip()[:128]
        if st not in ALLOWED_ENTITY_TYPES or tt not in ALLOWED_ENTITY_TYPES:
            continue
        if not sc or not tc:
            continue
        if (st, sc) not in seen_keys or (tt, tc) not in seen_keys:
            # Drop relations referencing entities we didn't extract — keeps
            # the graph honest. LLM sometimes hallucinates endpoints.
            continue
        relations.append(
            Relation(
                source_type=st,
                source_canonical=sc,
                edge_type=edge,
                target_type=tt,
                target_canonical=tc,
            )
        )

    return entities, relations


async def extract_entities(
    title: str | None, body: str | None
) -> tuple[list[Entity], list[Relation], str]:
    """Top-level entry. Tries LLM first (entities + relations), falls back
    to heuristic (entities only, no inter-entity relations).

    Returns (entities, relations, source) where source is 'llm' or 'heuristic'.
    """
    llm_result = await llm_extract(title, body)
    if llm_result is not None and llm_result[0]:
        entities, relations = llm_result
        return entities, relations, "llm"
    return heuristic_extract(title, body), [], "heuristic"
