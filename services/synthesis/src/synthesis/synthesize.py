"""Hourly thematic synthesis — agent-driven, with single-shot fallback.

Takes the last N hours of raw_documents and produces a list of *emerging
themes* (Concept nodes + IMPACTS edges in the AGE graph).

Primary path: `synthesis.agent.run_synthesis_agent` — drives the shared SDK
tool loop with read-only tools (recent_documents / existing_concepts /
existing_assets) so the LLM grounds themes in actual graph coverage and avoids
duplicates.

Fallback path: legacy single-shot LLM call (no tools). Triggered when the
subscription is unavailable OR the agent loop returns nothing parseable.
The persist step (Concept upsert + IMPACTS edges) is the same for both paths
— the LLM never touches the graph; the service does.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from graph.age import link_typed_edge, upsert_entity
from loguru import logger
from matrix_shared import call_claude_json, local_session_scope
from matrix_shared.models import RawDocument
from sqlalchemy import desc, select

from synthesis.agent import run_synthesis_agent
from synthesis.themes import Theme, extract_themes_json, themes_from_parsed

LLM_MODEL = "claude-sonnet-4-6"

MAX_DOCS_PER_RUN = 60       # context budget cap (fallback path)
TITLE_CHARS = 200
BODY_CHARS = 400            # snippet per doc


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


async def _single_shot_fallback(corpus: str, hours: float) -> list[Theme] | None:
    """Legacy path — kept as a safety net while the agent path is being
    validated against prod data."""
    system = (
        "You are a financial-news synthesis agent. Given a recent corpus of "
        "headlines + body snippets, identify 3-8 *emerging themes* — concepts "
        "that span multiple documents and would matter to a trader. For each "
        "theme, list the assets and companies it impacts (canonical tickers/"
        "names only). Skip themes supported by fewer than 2 distinct documents."
        "\n\nReturn ONLY a JSON object: "
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
    # call_claude_json already returns a dict; re-parse via extract_themes_json
    # is unnecessary, but themes_from_parsed handles validation/clamping.
    themes = themes_from_parsed(parsed)
    return themes or None


async def run_synthesis(window_hours: float = 6.0) -> int:
    """One synthesis cycle. Returns number of themes upserted."""
    docs = await _fetch_recent_docs(window_hours)
    if not docs:
        logger.info("synthesis: no recent documents")
        return 0

    # Primary path: agent tool loop. Reads docs + existing graph coverage on its own.
    themes = await run_synthesis_agent(window_hours)
    if themes:
        logger.info(f"synthesis: agent path produced {len(themes)} themes")
    else:
        # Fallback to single-shot with a packed corpus.
        corpus = _pack_corpus(docs)
        themes = await _single_shot_fallback(corpus, window_hours)
        if themes:
            logger.info(f"synthesis: single-shot fallback produced {len(themes)} themes")

    if not themes:
        logger.info("synthesis: no themes emerged")
        return 0

    for theme in themes:
        try:
            await upsert_entity("Concept", theme.canonical, theme.display)
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


# Backwards-compat re-export — anything that used `synthesis.synthesize.Theme`
# still works. The dataclass moved to themes.py during the agent conversion.
__all__ = ["Theme", "extract_themes_json", "run_synthesis", "themes_from_parsed"]
