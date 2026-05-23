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

import httpx
import orjson
from loguru import logger

from matrix_shared import get_settings

GATEWAY_URL = "https://ai-gateway.vercel.sh/v1/chat/completions"
LLM_MODEL = "anthropic/claude-haiku-4-5"
HTTP_TIMEOUT_S = 30.0


@dataclass(slots=True)
class Entity:
    type: str
    canonical: str  # canonical key — used for graph MERGE
    display: str
    props: dict[str, str] = field(default_factory=dict)


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


async def llm_extract(title: str | None, body: str | None) -> list[Entity] | None:
    """LLM-driven extraction; returns None if gateway unavailable/error."""
    api_key = get_settings().ai_gateway_api_key
    if not api_key:
        return None

    user = f"TITLE: {title or ''}\n\nBODY: {(body or '')[:4000]}"
    body_payload = {
        "model": LLM_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Extract entities from this financial news article. Return ONLY a JSON "
                    "object: {\"entities\":[{\"type\":\"Asset|Company|Person|Event|Concept\","
                    "\"canonical\":\"...\",\"display\":\"...\"}, ...]}. "
                    "Asset = canonical ticker (BTC, ETH, ...). Company = legal entity name. "
                    "Person = full name. Event = high-level category like 'ETF approval', "
                    "'hack', 'regulatory enforcement'. Concept = abstract theme. "
                    "Max 12 entities. Skip if uncertain."
                ),
            },
            {"role": "user", "content": user},
        ],
        "max_tokens": 700,
        "temperature": 0.1,
    }
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
            resp = await client.post(
                GATEWAY_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                content=orjson.dumps(body_payload),
            )
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning(f"llm extract gateway error: {e}")
        return None

    try:
        text = data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError):
        return None
    if text.startswith("```"):
        text = "\n".join(
            ln for ln in text.splitlines() if not ln.strip().startswith("```")
        ).strip()
    try:
        parsed = orjson.loads(text)
    except orjson.JSONDecodeError:
        return None
    raw_ents = parsed.get("entities") or []
    out: list[Entity] = []
    for e in raw_ents[:12]:
        if not isinstance(e, dict):
            continue
        t = str(e.get("type", "")).strip()
        if t not in ("Asset", "Company", "Person", "Event", "Concept"):
            continue
        canonical = str(e.get("canonical", "")).strip()[:128]
        display = str(e.get("display", canonical))[:200]
        if not canonical:
            continue
        out.append(Entity(type=t, canonical=canonical, display=display))
    return out


async def extract_entities(title: str | None, body: str | None) -> tuple[list[Entity], str]:
    """Top-level entry. Tries LLM first, falls back to heuristic.

    Returns (entities, source) where source is 'llm' or 'heuristic'.
    """
    llm_result = await llm_extract(title, body)
    if llm_result is not None and llm_result:
        return llm_result, "llm"
    return heuristic_extract(title, body), "heuristic"
