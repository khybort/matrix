"""Read-side graph queries used by the agent.

Each function runs a Cypher query against matrix_graph and pulls the
result into a Python-friendly shape. Two characteristics matter:

1. AGE requires the LOAD 'age' + SET search_path prelude on every
   transaction. asyncpg disallows multi-statement prepared queries,
   so prelude statements are sent separately.

2. AGE returns `agtype`, a JSON-y variant; we cast to text and parse.

Each cypher query runs in its own session_scope so a failure in one
does not abort a sibling transaction.

These functions are the *primary path* through which graph data
influences trading decisions. Until this module existed, the graph
was build-only and the agent didn't consult it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from loguru import logger
from matrix_shared import session_scope
from sqlalchemy import text

# Bullish / bearish keyword vocabularies.
BULLISH_KW = (
    "surge", "rally", "soar", "breakout", "approve", "approval", "etf",
    "adoption", "bullish", "high", "all-time", "ath", "buy", "accumulat",
    "partner", "upgrade",
)
BEARISH_KW = (
    "crash", "plunge", "drop", "ban", "hack", "exploit", "lawsuit",
    "investigation", "bearish", "selloff", "liquidat", "sell",
    "warning", "fraud", "freeze", "sanction",
)


def _polarity_from_text(text_: str) -> Decimal:
    t = (text_ or "").lower()
    pos = sum(1 for k in BULLISH_KW if k in t)
    neg = sum(1 for k in BEARISH_KW if k in t)
    if pos + neg == 0:
        return Decimal("0")
    return Decimal(pos - neg) / Decimal(pos + neg)


@dataclass(slots=True)
class GraphAssetContext:
    asset: str
    window_hours: float
    direct_mention_count: int = 0
    direct_polarity: Decimal = Decimal("0")
    recency_weighted_count: Decimal = Decimal("0")
    related_companies: list[tuple[str, int]] = field(default_factory=list)
    co_mentioned_assets: list[tuple[str, int]] = field(default_factory=list)
    contextual_polarity: Decimal = Decimal("0")
    n_contextual_documents: int = 0


def _agtype_strip(s: str) -> str:
    s = (s or "").strip()
    if s.startswith('"') and s.endswith('"'):
        return s[1:-1]
    return s


async def _exec_cypher(query_sql: str) -> list:
    """Run one Cypher query in its own session.

    A failure here raises; siblings are unaffected because they each open
    their own session_scope.
    """
    async with session_scope() as session:
        await session.execute(text("LOAD 'age'"))
        await session.execute(text("SET search_path = ag_catalog, public"))
        return list((await session.execute(text(query_sql))).all())


async def get_asset_context(
    asset_canonical: str, *, window_hours: float = 24.0
) -> GraphAssetContext:
    """Pull the recent graph context for one asset."""
    ctx = GraphAssetContext(asset=asset_canonical, window_hours=window_hours)
    now = datetime.now(UTC)
    cutoff = (now - timedelta(hours=window_hours)).isoformat()

    # 1) Direct mentions
    q_direct = (
        f"SELECT * FROM cypher('matrix_graph', $$ "
        f"MATCH (d:Document)-[m1:MENTIONS]->(a:Asset {{canonical: '{asset_canonical}'}}) "
        f"WHERE d.published_at >= '{cutoff}' "
        f"RETURN d.title, d.published_at "
        f"$$) AS (title agtype, published_at agtype)"
    )
    try:
        rows = await _exec_cypher(q_direct)
    except Exception as e:
        logger.warning(f"graph direct query failed for {asset_canonical}: {e}")
        rows = []

    ctx.direct_mention_count = len(rows)
    pol_sum = Decimal("0")
    pol_n = 0
    recency = Decimal("0")
    half_life_h = Decimal("6")
    for title_ag, pub_ag in rows:
        title = _agtype_strip(str(title_ag))
        pub = _agtype_strip(str(pub_ag))
        p = _polarity_from_text(title)
        if p != 0:
            pol_sum += p
            pol_n += 1
        try:
            pub_dt = datetime.fromisoformat(pub)
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=UTC)
            age_hours = Decimal((now - pub_dt).total_seconds() / 3600.0)
            weight = Decimal(2) ** (-(age_hours / half_life_h))
        except (ValueError, ArithmeticError):
            weight = Decimal("0.5")
        recency += weight
    if pol_n:
        ctx.direct_polarity = pol_sum / Decimal(pol_n)
    ctx.recency_weighted_count = recency

    # 2) Related companies — 2-hop, distinct edge variables m1/m2
    q_companies = (
        f"SELECT * FROM cypher('matrix_graph', $$ "
        f"MATCH (a:Asset {{canonical: '{asset_canonical}'}})<-[m1:MENTIONS]-(d:Document) "
        f"      -[m2:MENTIONS]->(c:Company) "
        f"WHERE d.published_at >= '{cutoff}' "
        f"RETURN c.canonical, count(d) ORDER BY count(d) DESC LIMIT 8 "
        f"$$) AS (canonical agtype, n agtype)"
    )
    try:
        for can_ag, n_ag in await _exec_cypher(q_companies):
            ctx.related_companies.append(
                (_agtype_strip(str(can_ag)), int(str(n_ag).strip().strip('"')))
            )
    except Exception as e:
        logger.warning(f"graph companies query for {asset_canonical}: {e}")

    # 3) Co-mentioned assets (excluding self)
    q_coassets = (
        f"SELECT * FROM cypher('matrix_graph', $$ "
        f"MATCH (a:Asset {{canonical: '{asset_canonical}'}})<-[m1:MENTIONS]-(d:Document) "
        f"      -[m2:MENTIONS]->(b:Asset) "
        f"WHERE d.published_at >= '{cutoff}' AND b.canonical <> '{asset_canonical}' "
        f"RETURN b.canonical, count(d) ORDER BY count(d) DESC LIMIT 6 "
        f"$$) AS (canonical agtype, n agtype)"
    )
    try:
        for can_ag, n_ag in await _exec_cypher(q_coassets):
            ctx.co_mentioned_assets.append(
                (_agtype_strip(str(can_ag)), int(str(n_ag).strip().strip('"')))
            )
    except Exception as e:
        logger.warning(f"graph co-assets query for {asset_canonical}: {e}")

    # 4) Contextual polarity — titles from documents about related companies
    if ctx.related_companies:
        companies_list = ", ".join(f"'{c[0]}'" for c in ctx.related_companies)
        q_ctx = (
            f"SELECT * FROM cypher('matrix_graph', $$ "
            f"MATCH (c:Company)<-[m1:MENTIONS]-(d:Document) "
            f"WHERE c.canonical IN [{companies_list}] AND d.published_at >= '{cutoff}' "
            f"RETURN DISTINCT d.title "
            f"$$) AS (title agtype)"
        )
        try:
            pol_sum = Decimal("0")
            pol_n = 0
            for (title_ag,) in await _exec_cypher(q_ctx):
                p = _polarity_from_text(_agtype_strip(str(title_ag)))
                if p != 0:
                    pol_sum += p
                    pol_n += 1
            ctx.n_contextual_documents = pol_n
            if pol_n:
                ctx.contextual_polarity = pol_sum / Decimal(pol_n)
        except Exception as e:
            logger.warning(f"graph context polarity for {asset_canonical}: {e}")

    return ctx


def base_token_to_asset(symbol: str) -> str:
    """BTCUSDT -> BTC, ETHUSDT -> ETH, etc. Matches graph canonical tickers."""
    for q in ("USDT", "USDC", "USD", "BUSD"):
        if symbol.endswith(q):
            return symbol[: -len(q)]
    return symbol
