"""Gradual re-extraction of heuristic-processed documents through the agent path.

Older rows may have `meta.graph_source != 'agent'` after a one-shot heuristic
pass. Backfill re-runs extraction on a small batch on a separate interval so
new-doc ingestion stays responsive and Cursor/agent cost stays bounded.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime

from loguru import logger
from matrix_shared import session_scope
from matrix_shared.models import RawDocument
from sqlalchemy import select, text

PROCESSED_KEY = "graph_processed_at"
SOURCE_KEY = "graph_source"
BACKFILL_AT_KEY = "graph_backfill_at"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class BackfillConfig:
    enabled: bool
    batch: int
    interval_s: float
    cooldown_s: float

    @classmethod
    def from_env(cls) -> BackfillConfig:
        return cls(
            enabled=_env_bool("GRAPH_BACKFILL_ENABLED", True),
            batch=max(1, int(os.environ.get("GRAPH_BACKFILL_BATCH", "2"))),
            interval_s=float(os.environ.get("GRAPH_BACKFILL_INTERVAL_S", "300")),
            cooldown_s=float(os.environ.get("GRAPH_BACKFILL_COOLDOWN_S", "3600")),
        )


async def count_pending() -> int:
    cfg = BackfillConfig.from_env()
    if not cfg.enabled:
        return 0
    async with session_scope() as session:
        row = await session.execute(
            text("""
                SELECT COUNT(*)::int
                FROM raw_documents
                WHERE (meta::jsonb ? :processed_key)
                  AND COALESCE(meta->>:source_key, 'heuristic') != 'agent'
                  AND (
                    NOT (meta::jsonb ? :backfill_key)
                    OR (meta->>:backfill_key)::timestamptz
                       < NOW() - make_interval(secs => :cooldown)
                  )
            """),
            {
                "processed_key": PROCESSED_KEY,
                "source_key": SOURCE_KEY,
                "backfill_key": BACKFILL_AT_KEY,
                "cooldown": cfg.cooldown_s,
            },
        )
        return int(row.scalar_one())


async def fetch_batch(limit: int) -> list[RawDocument]:
    cfg = BackfillConfig.from_env()
    async with session_scope() as session:
        rows = await session.execute(
            text("""
                SELECT id
                FROM raw_documents
                WHERE (meta::jsonb ? :processed_key)
                  AND COALESCE(meta->>:source_key, 'heuristic') != 'agent'
                  AND (
                    NOT (meta::jsonb ? :backfill_key)
                    OR (meta->>:backfill_key)::timestamptz
                       < NOW() - make_interval(secs => :cooldown)
                  )
                ORDER BY published_at ASC NULLS LAST
                LIMIT :lim
            """),
            {
                "processed_key": PROCESSED_KEY,
                "source_key": SOURCE_KEY,
                "backfill_key": BACKFILL_AT_KEY,
                "cooldown": cfg.cooldown_s,
                "lim": limit,
            },
        )
        ids = [r[0] for r in rows]
        if not ids:
            return []
        docs = list(
            (await session.execute(select(RawDocument).where(RawDocument.id.in_(ids))))
            .scalars()
            .all()
        )
    order = {doc_id: i for i, doc_id in enumerate(ids)}
    docs.sort(key=lambda d: order[d.id])
    return docs


async def touch_attempt(doc_id) -> None:
    async with session_scope() as session:
        doc = await session.get(RawDocument, doc_id)
        if doc is None:
            return
        new_meta = dict(doc.meta or {})
        new_meta[BACKFILL_AT_KEY] = datetime.now(UTC).isoformat()
        doc.meta = new_meta


async def backfill_tick(process_doc, *, limit: int | None = None) -> int:
    """Re-run agent extraction on up to `limit` heuristic-sourced documents."""
    cfg = BackfillConfig.from_env()
    if not cfg.enabled:
        return 0
    batch_size = limit if limit is not None else cfg.batch
    batch = await fetch_batch(batch_size)
    if not batch:
        return 0

    import asyncio

    for doc in batch:
        await touch_attempt(doc.id)

    # Agent path is expensive — one doc at a time, separate from new-doc concurrency.
    sem = asyncio.Semaphore(1)
    results = await asyncio.gather(
        *[process_doc(doc, sem) for doc in batch],
        return_exceptions=True,
    )
    ok = sum(1 for r in results if r is True)
    pending = await count_pending()
    logger.info(
        f"backfill tick: upgraded {ok}/{len(batch)} docs "
        f"({pending} heuristic rows still queued)"
    )
    return ok
