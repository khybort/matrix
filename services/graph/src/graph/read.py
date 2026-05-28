"""Read federated graph_signals from the SHARED tier.

When a local AGE has thin coverage for an asset, the agent can consult the
latest aggregate any PC has published. Returns the freshest row across
all node_ids within max_age_minutes; older rows are ignored to avoid
acting on stale federation data.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from loguru import logger
from matrix_shared import shared_session_scope
from matrix_shared.models import GraphSignal
from sqlalchemy import desc, select

from graph.queries import GraphAssetContext


async def get_remote_graph_signal(
    asset: str, *, max_age_minutes: float = 30.0
) -> GraphAssetContext | None:
    """Most recent published signal across all PCs, within max_age_minutes."""
    cutoff = datetime.now(UTC) - timedelta(minutes=max_age_minutes)
    try:
        async with shared_session_scope() as session:
            stmt = (
                select(GraphSignal)
                .where(GraphSignal.asset == asset)
                .where(GraphSignal.computed_at >= cutoff)
                .order_by(desc(GraphSignal.computed_at))
                .limit(1)
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
    except Exception as e:
        logger.warning(f"remote signal read failed for {asset}: {e}")
        return None

    if row is None:
        return None

    ctx = GraphAssetContext(asset=asset, window_hours=row.window_hours)
    ctx.direct_mention_count = row.direct_mention_count
    ctx.recency_weighted_count = Decimal(row.recency_weight)
    ctx.direct_polarity = Decimal(row.direct_polarity)
    ctx.contextual_polarity = Decimal(row.contextual_polarity)
    ctx.n_contextual_documents = row.n_contextual_documents
    # graph_signals stores companies/assets as flat lists; canonical name +
    # placeholder count of 1 keeps shape consistent with local AGE output.
    ctx.related_companies = [(c, 1) for c in (row.related_companies or [])]
    ctx.co_mentioned_assets = [(a, 1) for a in (row.co_mentioned_assets or [])]
    return ctx


def merge_contexts(
    local: GraphAssetContext, remote: GraphAssetContext
) -> GraphAssetContext:
    """Blend local + remote into a single context. Local data takes priority
    when both are non-empty; remote fills gaps."""
    merged = GraphAssetContext(asset=local.asset, window_hours=local.window_hours)

    # Counts: prefer the richer source
    merged.direct_mention_count = max(
        local.direct_mention_count, remote.direct_mention_count
    )
    merged.n_contextual_documents = max(
        local.n_contextual_documents, remote.n_contextual_documents
    )
    merged.recency_weighted_count = (
        local.recency_weighted_count
        if local.direct_mention_count > 0
        else remote.recency_weighted_count
    )

    # Polarities: weighted average by mention count when both have signal
    def _blend(local_v: Decimal, local_n: int, remote_v: Decimal, remote_n: int) -> Decimal:
        if local_n == 0 and remote_n == 0:
            return Decimal("0")
        if local_n == 0:
            return remote_v
        if remote_n == 0:
            return local_v
        total = Decimal(local_n + remote_n)
        return (local_v * Decimal(local_n) + remote_v * Decimal(remote_n)) / total

    merged.direct_polarity = _blend(
        local.direct_polarity,
        local.direct_mention_count,
        remote.direct_polarity,
        remote.direct_mention_count,
    )
    merged.contextual_polarity = _blend(
        local.contextual_polarity,
        local.n_contextual_documents,
        remote.contextual_polarity,
        remote.n_contextual_documents,
    )

    # Entity sets: union, dedup by canonical, sum counts where overlap
    def _union(
        a: list[tuple[str, int]], b: list[tuple[str, int]]
    ) -> list[tuple[str, int]]:
        out: dict[str, int] = {}
        for k, v in a:
            out[k] = out.get(k, 0) + v
        for k, v in b:
            out[k] = out.get(k, 0) + v
        return sorted(out.items(), key=lambda kv: -kv[1])

    merged.related_companies = _union(local.related_companies, remote.related_companies)
    merged.co_mentioned_assets = _union(
        local.co_mentioned_assets, remote.co_mentioned_assets
    )
    return merged
