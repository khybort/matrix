"""Graph extraction loop.

For each raw_document not yet processed:
    1. Extract entities (LLM if available, else heuristic)
    2. Upsert Document node into AGE
    3. Upsert each Entity node
    4. Link Document --MENTIONS--> Entity

Process tracking is via raw_documents.meta JSONB — we set
`meta.graph_processed_at` to mark a row as done. Re-processing is allowed
(MERGE is idempotent) and triggered by clearing that key.

Usage:
    uv run python -m graph.main                  # default 20s loop, batch 50
    uv run python -m graph.main --once --limit 10
    uv run python -m graph.main --reprocess      # ignore processed flag
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from datetime import UTC, datetime, timedelta

from loguru import logger
from matrix_shared import session_scope
from matrix_shared.models import RawDocument
from sqlalchemy import select, text

from graph.age import (
    graph_summary,
    link_mentions,
    link_typed_edge,
    upsert_document,
    upsert_entity,
)
from graph.backfill import BackfillConfig, backfill_tick, count_pending
from graph.chunking import ChunkConfig, plan_semantic_chunks
from graph.extract import extract_entities, heuristic_extract
from graph.publish import DEFAULT_ASSETS, publish_all

DEFAULT_INTERVAL_S = float(os.environ.get("GRAPH_INTERVAL_S", "20"))
DEFAULT_BATCH = max(1, int(os.environ.get("GRAPH_BATCH", "50")))
DEFAULT_CONCURRENCY = max(1, int(os.environ.get("GRAPH_CONCURRENCY", "6")))
DEFAULT_STALE_HEURISTIC_DAYS = float(os.environ.get("GRAPH_STALE_HEURISTIC_DAYS", "7"))
DEFAULT_BACKFILL_PAUSE_UNPROCESSED = max(
    0, int(os.environ.get("GRAPH_BACKFILL_PAUSE_UNPROCESSED", "20"))
)
DEFAULT_PUBLISH_INTERVAL_S = 120.0  # publish federated aggregates twice / extract loop
PROCESSED_KEY = "graph_processed_at"


async def count_unprocessed() -> int:
    async with session_scope() as session:
        row = await session.execute(
            text("""
                SELECT COUNT(*)::int
                FROM raw_documents
                WHERE NOT (meta::jsonb ? :key)
            """),
            {"key": PROCESSED_KEY},
        )
        return int(row.scalar_one())


async def _fetch_batch(limit: int, reprocess: bool) -> list[RawDocument]:
    async with session_scope() as session:
        if reprocess:
            rows = await session.execute(
                text("""
                    SELECT id
                    FROM raw_documents
                    ORDER BY published_at DESC NULLS LAST
                    LIMIT :lim
                """),
                {"lim": limit},
            )
        else:
            rows = await session.execute(
                text("""
                    SELECT id
                    FROM raw_documents
                    WHERE NOT (meta::jsonb ? :key)
                    ORDER BY published_at DESC NULLS LAST
                    LIMIT :lim
                """),
                {"key": PROCESSED_KEY, "lim": limit},
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


def _doc_is_stale(doc: RawDocument) -> bool:
    if DEFAULT_STALE_HEURISTIC_DAYS <= 0 or doc.published_at is None:
        return False
    pub = doc.published_at
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=UTC)
    return pub < datetime.now(UTC) - timedelta(days=DEFAULT_STALE_HEURISTIC_DAYS)


async def _mark_processed(
    doc_id, source: str, *, chunk_count: int = 1
) -> None:
    async with session_scope() as session:
        doc = await session.get(RawDocument, doc_id)
        if doc is None:
            return
        new_meta = dict(doc.meta or {})
        new_meta[PROCESSED_KEY] = datetime.now(UTC).isoformat()
        new_meta["graph_source"] = source
        new_meta["graph_chunks"] = chunk_count
        doc.meta = new_meta


def _chunk_count(body: str | None) -> int:
    cfg = ChunkConfig.from_env()
    text = (body or "").strip()
    if not cfg.enabled or len(text) <= cfg.max_chars:
        return 1
    return max(1, len(plan_semantic_chunks(text, cfg)))


async def _process_doc(doc, sem: asyncio.Semaphore) -> bool:
    async with sem:
        chunks = _chunk_count(doc.body)
        try:
            if _doc_is_stale(doc):
                entities = heuristic_extract(doc.title, doc.body)
                relations = []
                source = "heuristic"
            else:
                entities, relations, source = await extract_entities(doc.title, doc.body)
        except Exception as e:
            logger.exception(f"extract failed for {doc.id}: {e}")
            return False
        if not entities:
            await _mark_processed(doc.id, source, chunk_count=chunks)
            return False
        try:
            await upsert_document(
                doc.id,
                doc.source,
                doc.title,
                doc.url,
                doc.published_at.isoformat() if doc.published_at else None,
            )
            for ent in entities:
                await upsert_entity(ent.type, ent.canonical, ent.display)
                await link_mentions(doc.id, ent.type, ent.canonical)
            for rel in relations:
                try:
                    await link_typed_edge(
                        rel.source_type,
                        rel.source_canonical,
                        rel.edge_type,
                        rel.target_type,
                        rel.target_canonical,
                        source_doc_id=doc.id,
                    )
                except Exception as e:
                    logger.warning(f"relation upsert failed: {e}")
        except Exception as e:
            logger.exception(f"graph upsert failed for {doc.id}: {e}")
            return False
        await _mark_processed(doc.id, source, chunk_count=chunks)
        logger.info(
            f"processed {doc.id} ({doc.source}): {len(entities)} entities, "
            f"{len(relations)} relations (src={source}, chunks={chunks})"
        )
        return True


async def _tick(limit: int, reprocess: bool) -> int:
    backlog = await count_unprocessed()
    batch = await _fetch_batch(limit, reprocess)
    if not batch:
        if backlog and not reprocess:
            logger.warning(f"tick: {backlog} unprocessed but fetch returned 0")
        return 0
    if backlog:
        logger.info(f"tick: backlog {backlog}, batch {len(batch)}")

    sem = asyncio.Semaphore(DEFAULT_CONCURRENCY)
    results = await asyncio.gather(
        *[_process_doc(doc, sem) for doc in batch], return_exceptions=True
    )
    ok = sum(1 for r in results if r is True)
    if ok:
        remaining = await count_unprocessed()
        logger.info(f"tick: processed {ok}/{len(batch)} ({remaining} left)")
    return ok


async def run(
    interval_s: float,
    limit: int,
    publish_interval_s: float = DEFAULT_PUBLISH_INTERVAL_S,
    assets: tuple[str, ...] = DEFAULT_ASSETS,
) -> None:
    stop = asyncio.Event()
    backfill_cfg = BackfillConfig.from_env()
    last_backfill = 0.0

    def _handle_signal(*_: object) -> None:
        logger.info("shutdown signal received")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    if backfill_cfg.enabled:
        pending = await count_pending()
        logger.info(
            f"graph backfill enabled: batch={backfill_cfg.batch} "
            f"interval={backfill_cfg.interval_s}s cooldown={backfill_cfg.cooldown_s}s "
            f"pending={pending}"
        )

    last_publish = 0.0
    while not stop.is_set():
        loop_started = asyncio.get_event_loop().time()
        try:
            n = await _tick(limit, reprocess=False)
        except Exception as e:
            logger.exception(f"tick failed: {e}")

        if (
            backfill_cfg.enabled
            and loop_started - last_backfill >= backfill_cfg.interval_s
        ):
            unprocessed = await count_unprocessed()
            if unprocessed > DEFAULT_BACKFILL_PAUSE_UNPROCESSED:
                logger.debug(
                    f"backfill paused: {unprocessed} unprocessed "
                    f"(resumes at <= {DEFAULT_BACKFILL_PAUSE_UNPROCESSED})"
                )
            else:
                try:
                    await backfill_tick(_process_doc)
                    last_backfill = loop_started
                except Exception as e:
                    logger.exception(f"backfill tick failed: {e}")
                    last_backfill = loop_started

        if loop_started - last_publish >= publish_interval_s:
            try:
                published = await publish_all(assets)
                if published:
                    logger.info(f"publish: {published} asset signal(s) flushed to shared tier")
            except Exception as e:
                logger.exception(f"publish failed: {e}")
            last_publish = loop_started

        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except TimeoutError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Matrix graph extraction")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_S)
    parser.add_argument(
        "--publish-interval", type=float, default=DEFAULT_PUBLISH_INTERVAL_S,
        help=f"Seconds between graph_signals publishes (default {DEFAULT_PUBLISH_INTERVAL_S})",
    )
    parser.add_argument(
        "--assets", nargs="*", default=list(DEFAULT_ASSETS),
        help="Asset canonical tickers to publish (BTC ETH SOL ...)",
    )
    parser.add_argument("--limit", type=int, default=DEFAULT_BATCH)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--reprocess", action="store_true")
    parser.add_argument(
        "--backfill-once",
        action="store_true",
        help="Run one backfill batch (heuristic→agent upgrade) and exit",
    )
    parser.add_argument(
        "--backfill-limit",
        type=int,
        default=None,
        help="Override GRAPH_BACKFILL_BATCH for --backfill-once",
    )
    parser.add_argument("--summary", action="store_true", help="Print graph counts and exit")
    parser.add_argument(
        "--publish-once", action="store_true",
        help="Run one publish cycle (computes & flushes asset signals) and exit",
    )
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level: <5} | {message}")

    if args.summary:
        async def _s():
            counts = await graph_summary()
            for k, v in counts.items():
                print(f"{k}: {v}")
        asyncio.run(_s())
        return

    if args.publish_once:
        async def _p():
            n = await publish_all(args.assets)
            logger.info(f"publish-once: {n} non-empty signals flushed")
        asyncio.run(_p())
        return

    if args.backfill_once:
        async def _bf():
            n = await backfill_tick(_process_doc, limit=args.backfill_limit)
            logger.info(f"backfill-once: upgraded {n} document(s)")
        asyncio.run(_bf())
        return

    logger.info(
        f"graph start: limit={args.limit} interval={args.interval}s "
        f"concurrency={DEFAULT_CONCURRENCY} once={args.once} reprocess={args.reprocess} "
        f"publish_interval={args.publish_interval}s assets={args.assets}"
    )
    if args.once:
        asyncio.run(_tick(args.limit, args.reprocess))
    else:
        asyncio.run(run(args.interval, args.limit, args.publish_interval, tuple(args.assets)))


if __name__ == "__main__":
    main()
