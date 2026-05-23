"""News RSS poller — ingests crypto news feeds into raw_documents.

Polls a handful of free RSS sources every POLL_INTERVAL_S seconds. Articles
are deduped by (source, external_id) via the unique constraint on raw_documents,
so we can re-poll safely.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import signal
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import feedparser
import httpx
from loguru import logger
from sqlalchemy.dialects.postgresql import insert as pg_insert

from matrix_shared import session_scope
from matrix_shared.models import RawDocument

POLL_INTERVAL_S = 300  # 5 min — most crypto RSS feeds update every 5-15 min
HTTP_TIMEOUT_S = 20.0
USER_AGENT = "Matrix/0.0.1 (research; +https://github.com/local)"


@dataclass(slots=True)
class FeedSource:
    name: str  # short source key, e.g. "coindesk"
    url: str


DEFAULT_FEEDS: list[FeedSource] = [
    FeedSource(name="coindesk", url="https://www.coindesk.com/arc/outboundfeeds/rss/"),
    FeedSource(name="theblock", url="https://www.theblock.co/rss.xml"),
    FeedSource(name="decrypt", url="https://decrypt.co/feed"),
    FeedSource(name="cointelegraph", url="https://cointelegraph.com/rss"),
]


@dataclass(slots=True)
class Article:
    source: str
    external_id: str
    url: str | None
    title: str | None
    body: str | None
    published_at: datetime | None
    meta: dict[str, Any]


async def fetch_feed(client: httpx.AsyncClient, src: FeedSource) -> list[Article]:
    """Fetch + parse one RSS source. Returns Articles ready for upsert."""
    try:
        resp = await client.get(src.url, timeout=HTTP_TIMEOUT_S)
        resp.raise_for_status()
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning(f"feed fetch error {src.name}: {e}")
        return []

    parsed = feedparser.parse(resp.content)
    if parsed.bozo:
        logger.debug(f"feed {src.name} parse warning: {parsed.bozo_exception}")

    articles: list[Article] = []
    for entry in parsed.entries:
        article = _entry_to_article(src.name, entry)
        if article is not None:
            articles.append(article)
    return articles


def _entry_to_article(source: str, entry: Any) -> Article | None:
    # Use the entry's id / guid; fall back to a hash of (title, link) so we
    # remain idempotent across re-polls.
    external_id = (
        getattr(entry, "id", None)
        or getattr(entry, "guid", None)
        or hashlib.sha256(
            (str(getattr(entry, "title", "")) + str(getattr(entry, "link", ""))).encode()
        ).hexdigest()
    )
    if not external_id:
        return None

    title = getattr(entry, "title", None) or None
    url = getattr(entry, "link", None) or None
    body = (
        getattr(entry, "summary", None)
        or getattr(entry, "description", None)
        or None
    )

    published_at: datetime | None = None
    for attr in ("published_parsed", "updated_parsed"):
        struct = getattr(entry, attr, None)
        if struct is not None:
            try:
                published_at = datetime(*struct[:6], tzinfo=UTC)
                break
            except (TypeError, ValueError):
                continue

    meta: dict[str, Any] = {}
    if (author := getattr(entry, "author", None)):
        meta["author"] = author
    if (tags := getattr(entry, "tags", None)):
        meta["tags"] = [t.term for t in tags if hasattr(t, "term")]

    return Article(
        source=source,
        external_id=str(external_id)[:255],
        url=url,
        title=title,
        body=body,
        published_at=published_at,
        meta=meta,
    )


async def upsert_articles(articles: list[Article]) -> int:
    """Insert articles with ON CONFLICT DO NOTHING. Returns inserted row count."""
    if not articles:
        return 0
    rows = [
        {
            "source": a.source,
            "external_id": a.external_id,
            "url": a.url,
            "title": a.title,
            "body": a.body,
            "published_at": a.published_at,
            "meta": a.meta,
        }
        for a in articles
    ]
    async with session_scope() as session:
        stmt = pg_insert(RawDocument).values(rows)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["source", "external_id"]
        )
        result = await session.execute(stmt)
        return result.rowcount or 0


async def run(feeds: list[FeedSource], *, interval_s: float = POLL_INTERVAL_S) -> None:
    stop = asyncio.Event()

    def _handle_signal(*_: object) -> None:
        logger.info("shutdown signal received")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT}, follow_redirects=True
    ) as client:
        while not stop.is_set():
            all_articles: list[Article] = []
            tasks = [fetch_feed(client, src) for src in feeds]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for src, res in zip(feeds, results, strict=True):
                if isinstance(res, BaseException):
                    logger.warning(f"feed {src.name} fetch failed: {res}")
                    continue
                logger.info(f"feed {src.name}: {len(res)} articles")
                all_articles.extend(res)
            inserted = await upsert_articles(all_articles)
            logger.info(
                f"poll cycle: fetched={len(all_articles)} new={inserted}; "
                f"sleep {interval_s:.0f}s"
            )
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_s)
            except TimeoutError:
                pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Matrix news RSS poller")
    parser.add_argument(
        "--interval",
        type=float,
        default=POLL_INTERVAL_S,
        help="Poll interval seconds (default 300)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Poll one cycle and exit (useful for smoketest)",
    )
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level: <5} | {message}")
    logger.info(f"news start: feeds={[f.name for f in DEFAULT_FEEDS]} once={args.once}")
    if args.once:
        asyncio.run(_run_once())
    else:
        asyncio.run(run(DEFAULT_FEEDS, interval_s=args.interval))


async def _run_once() -> None:
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT}, follow_redirects=True
    ) as client:
        all_articles: list[Article] = []
        for src in DEFAULT_FEEDS:
            articles = await fetch_feed(client, src)
            logger.info(f"feed {src.name}: {len(articles)} articles")
            all_articles.extend(articles)
        inserted = await upsert_articles(all_articles)
        logger.info(f"once: fetched={len(all_articles)} new={inserted}")


if __name__ == "__main__":
    main()
