"""Publish local AGE-derived asset context as denormalized aggregates
into the SHARED tier (Neon).

The agent on this PC already queries local AGE directly. The point of
publishing aggregates is so that:

1. Other PCs whose local graph hasn't seen an asset can read the latest
   aggregate from any PC that has.
2. The dashboard (which reads SHARED) can show graph context without
   each PC needing to expose its local DB.
3. We retain an audit trail of how the graph looked over time per asset,
   which becomes valuable training data for future reflection agents.

Writes are append-only — readers always pull the most recent row per asset.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal

from loguru import logger

from matrix_shared import get_settings, shared_session_scope
from matrix_shared.models import GraphSignal

from graph.queries import GraphAssetContext, get_asset_context

DEFAULT_ASSETS = ("BTC", "ETH", "SOL", "XRP", "DOGE", "MATIC", "ADA")


async def publish_signal(asset: str, *, window_hours: float = 24.0) -> GraphAssetContext | None:
    """Compute context for one asset from local AGE and append a row to the
    shared graph_signals table. Returns the computed context (or None on error)."""
    settings = get_settings()
    node_id = settings.node_id

    t0 = time.perf_counter()
    try:
        ctx = await get_asset_context(asset, window_hours=window_hours)
    except Exception as e:
        logger.warning(f"publish: get_asset_context failed for {asset}: {e}")
        return None
    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    # Skip publishing when local graph has truly nothing — saves Neon storage
    # and keeps the freshness signal meaningful for consumers.
    if (
        ctx.direct_mention_count == 0
        and not ctx.related_companies
        and not ctx.co_mentioned_assets
    ):
        return ctx

    async with shared_session_scope() as session:
        session.add(
            GraphSignal(
                node_id=node_id,
                asset=asset,
                computed_at=datetime.now(UTC),
                window_hours=float(window_hours),
                direct_mention_count=ctx.direct_mention_count,
                recency_weight=ctx.recency_weighted_count,
                direct_polarity=ctx.direct_polarity,
                contextual_polarity=ctx.contextual_polarity,
                n_contextual_documents=ctx.n_contextual_documents,
                related_companies=[c for c, _ in ctx.related_companies],
                co_mentioned_assets=[a for a, _ in ctx.co_mentioned_assets],
                computed_in_ms=elapsed_ms,
            )
        )
    logger.info(
        f"published {asset}: mentions={ctx.direct_mention_count} "
        f"pol={ctx.direct_polarity:.2f} companies={len(ctx.related_companies)} "
        f"co={len(ctx.co_mentioned_assets)} ({elapsed_ms}ms)"
    )
    return ctx


async def publish_all(assets: Iterable[str] = DEFAULT_ASSETS) -> int:
    """Publish one row per asset. Returns count of non-empty publishes."""
    n = 0
    for asset in assets:
        ctx = await publish_signal(asset)
        if ctx is not None and (
            ctx.direct_mention_count > 0
            or ctx.related_companies
            or ctx.co_mentioned_assets
        ):
            n += 1
    return n
