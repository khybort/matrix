"""Hourly thematic synthesis.

Takes the last N hours of raw_documents, sends their titles + body
samples to an LLM, gets back a list of *emerging themes* with the
assets/companies each one impacts. Each theme becomes a Concept node
in the AGE graph, linked via IMPACTS edges to mentioned entities.

Without AI_GATEWAY_API_KEY this module is a no-op — keyword-based
theme detection is far too noisy to be worth the complexity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import desc, select

from matrix_shared import call_claude_json, local_session_scope
from matrix_shared.models import RawDocument

from graph.age import link_typed_edge, upsert_entity

LLM_MODEL = "claude-haiku-4-5"

MAX_DOCS_PER_RUN = 60       # context budget cap
TITLE_CHARS = 200
BODY_CHARS = 400            # snippet per doc


@dataclass(slots=True)
class Theme:
    canonical: str           # short slug, used as Concept canonical
    display: str             # human-readable name
    summary: str             # 1-2 sentence rationale
    impacted_assets: list[str] = field(default_factory=list)     # ["BTC","ETH"]
    impacted_companies: list[str] = field(default_factory=list)


async def _fetch_recent_docs(hours: float) -> list[RawDocument]:
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    async with local_session_scope() as session:
        stmt = (
            select(RawDocument)
            .where(RawDocument.published_at >= cutoff)
            .order_by(desc(RawDocument.published_at))
            .limit(MAX_DOCS_PER_RUN)
        )
        return list((await session.execute(stmt)).scalars())


def _pack_corpus(docs: list[RawDocument]) -> str:
    lines: list[str] = []
    for i, d in enumerate(docs, 1):
        title = (d.title or "")[:TITLE_CHARS]
        body = (d.body or "")[:BODY_CHARS]
        src = d.source
        lines.append(f"[{i}] ({src}) {title}\n    {body}".rstrip())
    return "\n\n".join(lines)


async def _call_llm(corpus: str, hours: float) -> list[Theme] | None:
    system = (
        "You are a financial-news synthesis agent. Given a recent "
        "corpus of headlines + body snippets, identify 3-8 *emerging "
        "themes* — concepts that span multiple documents and would "
        "matter to a trader. For each theme, list the assets and "
        "companies it impacts (canonical tickers/names only). Skip "
        "themes supported by fewer than 2 distinct documents.\n\n"
        "Return ONLY a JSON object: "
        '{"themes":[{"canonical":"<slug>","display":"<short name>",'
        '"summary":"<1-2 sentences>","impacted_assets":["BTC",...],'
        '"impacted_companies":["BlackRock",...]}]}'
    )
    user = f"Window: last {hours:.0f}h. Documents:\n\n{corpus}"
    parsed = await call_claude_json(
        system=system, user=user, model=LLM_MODEL, max_tokens=1500, temperature=0.2,
    )
    if parsed is None:
        return None

    raw_themes = parsed.get("themes") or []
    themes: list[Theme] = []
    for t in raw_themes[:8]:
        if not isinstance(t, dict):
            continue
        canonical = str(t.get("canonical", "")).strip()[:96]
        display = str(t.get("display", canonical))[:200]
        summary = str(t.get("summary", ""))[:1000]
        if not canonical:
            continue
        assets = [str(x).strip()[:32] for x in (t.get("impacted_assets") or []) if x]
        companies = [str(x).strip()[:128] for x in (t.get("impacted_companies") or []) if x]
        themes.append(
            Theme(
                canonical=canonical,
                display=display,
                summary=summary,
                impacted_assets=assets,
                impacted_companies=companies,
            )
        )
    return themes


async def run_synthesis(window_hours: float = 6.0) -> int:
    """One synthesis cycle. Returns number of themes upserted (0 if no key or
    no recent docs).
    """
    docs = await _fetch_recent_docs(window_hours)
    if not docs:
        logger.info("synthesis: no recent documents")
        return 0

    corpus = _pack_corpus(docs)
    themes = await _call_llm(corpus, window_hours)
    if themes is None:
        logger.info("synthesis: LLM unavailable or returned no parseable themes")
        return 0
    if not themes:
        logger.info("synthesis: no themes emerged")
        return 0

    for theme in themes:
        try:
            # Concept node — keyed by canonical slug. display + summary stored
            # via the upsert helper (only display is set on the node today; we
            # don't have a generic prop sink yet, which is fine for v1).
            await upsert_entity("Concept", theme.canonical, theme.display)
            # IMPACTS edges
            for asset in theme.impacted_assets:
                await link_typed_edge(
                    "Concept", theme.canonical, "IMPACTS", "Asset", asset
                )
            for company in theme.impacted_companies:
                await link_typed_edge(
                    "Concept", theme.canonical, "IMPACTS", "Company", company
                )
        except Exception as e:
            logger.warning(f"synthesis upsert failed for theme {theme.canonical}: {e}")
            continue
        logger.info(
            f"synthesis: theme={theme.canonical} impacts="
            f"assets={theme.impacted_assets} companies={theme.impacted_companies}"
        )
    return len(themes)
