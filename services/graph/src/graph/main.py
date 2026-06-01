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
    uv run python -m graph.main                  # default 60s loop, batch 25
    uv run python -m graph.main --once --limit 10
    uv run python -m graph.main --reprocess      # ignore processed flag
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from datetime import UTC, datetime

from loguru import logger
from matrix_shared import session_scope
from matrix_shared.models import RawDocument
from sqlalchemy import desc, select

from graph.age import (
    graph_summary,
    link_mentions,
    link_typed_edge,
    upsert_document,
    upsert_entity,
)
from graph.extract import extract_entities
from graph.publish import DEFAULT_ASSETS, publish_all

DEFAULT_INTERVAL_S = 60.0
DEFAULT_BATCH = 25
DEFAULT_PUBLISH_INTERVAL_S = 120.0  # publish federated aggregates twice / 60s extract loop
PROCESSED_KEY = "graph_processed_at"


async def _fetch_batch(limit: int, reprocess: bool) -> list[RawDocument]:
    async with session_scope() as session:
        stmt = select(RawDocument).order_by(desc(RawDocument.published_at)).limit(limit)
        rows = list((await session.execute(stmt)).scalars())
    if reprocess:
        return rows
    return [r for r in rows if PROCESSED_KEY not in (r.meta or {})]


async def _mark_processed(doc_id, source: str) -> None:
    async with session_scope() as session:
        doc = await session.get(RawDocument, doc_id)
        if doc is None:
            return
        new_meta = dict(doc.meta or {})
        new_meta[PROCESSED_KEY] = datetime.now(UTC).isoformat()
        new_meta["graph_source"] = source
        doc.meta = new_meta


async def _process_doc(doc, sem: asyncio.Semaphore) -> bool:
    async with sem:
        try:
            entities, relations, source = await extract_entities(doc.title, doc.body)
        except Exception as e:
            logger.exception(f"extract failed for {doc.id}: {e}")
            return False
        if not entities:
            await _mark_processed(doc.id, source)
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
        await _mark_processed(doc.id, source)
        logger.info(
            f"processed {doc.id} ({doc.source}): {len(entities)} entities, "
            f"{len(relations)} relations (src={source})"
        )
        return True


async def _tick(limit: int, reprocess: bool) -> int:
    batch = await _fetch_batch(limit, reprocess)
    if not batch:
        return 0

    # 4 concurrent agent loops — rate_limiter inside extract_entities throttles
    # actual Bedrock throughput; the semaphore caps in-flight doc coroutines.
    sem = asyncio.Semaphore(4)
    results = await asyncio.gather(
        *[_process_doc(doc, sem) for doc in batch], return_exceptions=True
    )
    return sum(1 for r in results if r is True)


async def run(
    interval_s: float,
    limit: int,
    publish_interval_s: float = DEFAULT_PUBLISH_INTERVAL_S,
    assets: tuple[str, ...] = DEFAULT_ASSETS,
) -> None:
    stop = asyncio.Event()

    def _handle_signal(*_: object) -> None:
        logger.info("shutdown signal received")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    last_publish = 0.0
    while not stop.is_set():
        loop_started = asyncio.get_event_loop().time()
        try:
            n = await _tick(limit, reprocess=False)
            if n:
                logger.info(f"tick: processed {n} documents")
        except Exception as e:
            logger.exception(f"tick failed: {e}")

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

    logger.info(
        f"graph start: limit={args.limit} once={args.once} reprocess={args.reprocess} "
        f"publish_interval={args.publish_interval}s assets={args.assets}"
    )
    if args.once:
        asyncio.run(_tick(args.limit, args.reprocess))
    else:
        asyncio.run(run(args.interval, args.limit, args.publish_interval, tuple(args.assets)))


if __name__ == "__main__":
    main()
