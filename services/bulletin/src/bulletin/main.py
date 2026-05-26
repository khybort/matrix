"""bulletin daemon — generates + persists weekly issues.

Modes:
  default loop  — sleep, generate, sleep again (DEFAULT_INTERVAL_S).
  --once        — generate one issue, exit.
  --list        — print recent issues (id, slug, status), exit.
  --publish SLUG — flip a draft to published.

Status semantics:
  draft     — generated, not visible at /bulletin
  published — visible at /bulletin
  archived  — visible only at the slug URL, hidden from /bulletin

Default cadence ships drafts. The operator publishes when they're happy.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import desc, select

from matrix_shared import shared_session_scope
from matrix_shared.models import BulletinIssue

from bulletin.issue import compose
from bulletin.state import collect_snapshot

DEFAULT_INTERVAL_S = 7 * 24 * 3600.0  # weekly by default
DEFAULT_WINDOW_DAYS = 7
AUTO_PUBLISH = os.environ.get("BULLETIN_AUTO_PUBLISH", "false").strip().lower() == "true"


async def generate_one(window_days: int = DEFAULT_WINDOW_DAYS) -> str:
    """Collect state → compose → INSERT. Returns the slug."""
    snap = await collect_snapshot(window_days)
    composed = await compose(snap)
    now = datetime.now(timezone.utc)
    async with shared_session_scope() as session:
        issue = BulletinIssue(
            slug=composed.slug,
            title=composed.title,
            summary=composed.summary,
            body_md=composed.body_md,
            issue_date=now.date(),
            status="published" if AUTO_PUBLISH else "draft",
            model=composed.model,
            model_cost_usd=composed.model_cost_usd,
            generated_at=now,
            published_at=now if AUTO_PUBLISH else None,
        )
        session.add(issue)
    logger.info(
        f"generated issue slug={composed.slug} "
        f"status={'published' if AUTO_PUBLISH else 'draft'} "
        f"model={composed.model or 'template'}"
    )
    return composed.slug


async def list_recent(limit: int = 10) -> None:
    async with shared_session_scope() as session:
        rows = (
            await session.execute(
                select(BulletinIssue).order_by(desc(BulletinIssue.generated_at)).limit(limit)
            )
        ).scalars().all()
    if not rows:
        print("(no issues yet)")
        return
    print(f"{'slug':<48} {'date':<12} {'status':<10} title")
    print("-" * 110)
    for r in rows:
        print(f"{r.slug:<48} {r.issue_date.isoformat():<12} {r.status:<10} {r.title}")


async def publish(slug: str) -> bool:
    """Flip a draft to published. Returns True iff a row was updated."""
    now = datetime.now(timezone.utc)
    async with shared_session_scope() as session:
        row = (
            await session.execute(
                select(BulletinIssue).where(BulletinIssue.slug == slug).limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            logger.error(f"publish: slug {slug!r} not found")
            return False
        if row.status == "published":
            logger.info(f"publish: {slug} already published; no-op")
            return False
        row.status = "published"
        row.published_at = now
    logger.info(f"published: {slug}")
    return True


async def run(interval_s: float, window_days: int) -> None:
    stop = asyncio.Event()

    def _handle_signal(*_: object) -> None:
        logger.info("shutdown signal received")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    while not stop.is_set():
        try:
            await generate_one(window_days)
        except Exception as e:
            logger.exception(f"generate failed (non-fatal): {e}")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except TimeoutError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Matrix bulletin daemon")
    parser.add_argument(
        "--interval", type=float, default=DEFAULT_INTERVAL_S,
        help=f"Loop interval seconds (default {int(DEFAULT_INTERVAL_S)} = weekly)",
    )
    parser.add_argument(
        "--window-days", type=int, default=DEFAULT_WINDOW_DAYS,
        help=f"Window for state snapshot (default {DEFAULT_WINDOW_DAYS})",
    )
    parser.add_argument("--once", action="store_true",
                        help="Generate one issue and exit")
    parser.add_argument("--list", action="store_true", dest="list_recent",
                        help="List recent issues and exit")
    parser.add_argument("--publish", metavar="SLUG", default=None,
                        help="Flip a draft to published and exit")
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level: <5} | {message}")

    if args.list_recent:
        asyncio.run(list_recent())
        return
    if args.publish:
        ok = asyncio.run(publish(args.publish))
        sys.exit(0 if ok else 1)
    if args.once:
        slug = asyncio.run(generate_one(args.window_days))
        print(slug)
        return

    logger.info(
        f"bulletin start: interval={args.interval}s window={args.window_days}d "
        f"auto_publish={AUTO_PUBLISH}"
    )
    asyncio.run(run(args.interval, args.window_days))


if __name__ == "__main__":
    main()
