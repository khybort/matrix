"""Director daemon: hourly system review + operator brief.

Usage:
    uv run python -m director.main                 # hourly loop
    uv run python -m director.main --once          # one tick, print brief, exit
    uv run python -m director.main --digest-only   # no LLM, just the digest
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import os
import signal
import sys

import httpx
from loguru import logger

from director.agent import run_director_tick
from director.digest import collect_digest, render_brief

DEFAULT_INTERVAL_S = 3600.0


async def push_telegram(text: str) -> int:
    """Best-effort brief delivery to the allow-listed chats (same env as notify)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    raw = os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "").strip()
    if not token or not raw:
        return 0
    sent = 0
    async with httpx.AsyncClient(timeout=15) as client:
        for chat in [c.strip() for c in raw.split(",") if c.strip()]:
            try:
                r = await client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat, "text": text[:4000], "disable_web_page_preview": True},
                )
                sent += 1 if r.status_code == 200 else 0
            except Exception as e:  # noqa: BLE001
                logger.warning(f"telegram push failed: {e}")
    return sent


async def tick(*, digest_only: bool = False) -> str:
    digest = await collect_digest()
    if digest_only:
        brief = render_brief(digest)
        mode = "digest"
    else:
        result = await run_director_tick(digest)
        brief, mode = result["brief"], result["mode"]
        if result["actions"]:
            logger.warning(f"director actions ({mode}): {result['actions']}")
    logger.info(f"director brief [{mode}]:\n{brief}")
    n = await push_telegram(brief)
    if n:
        logger.info(f"brief pushed to {n} chat(s)")
    return brief


async def run(interval_s: float) -> None:
    stop = asyncio.Event()

    def _handle_signal(*_: object) -> None:
        logger.info("shutdown signal received")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    # A restart is not a reason to review: with hot reload every shared-code
    # save restarted this daemon and it ran an LLM review each time (4 briefs
    # in 30 min on 2026-09-13). Resume the hourly cadence from the last brief.
    try:
        from matrix_shared.usage_ledger import last_record_ts
        last = last_record_ts(service="director", session="director")
        if last is not None:
            elapsed = (datetime.now(UTC) - last).total_seconds()
            if elapsed < interval_s:
                wait = interval_s - elapsed
                logger.info(f"director: last review {elapsed/60:.0f} min ago; first tick in {wait/60:.0f} min")
                try:
                    await asyncio.wait_for(stop.wait(), timeout=wait)
                except TimeoutError:
                    pass
    except Exception as e:  # noqa: BLE001
        logger.debug(f"director: cadence probe failed ({e}); ticking now")

    while not stop.is_set():
        try:
            await tick()
        except Exception as e:
            logger.exception(f"director tick failed: {e}")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except TimeoutError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Matrix Director")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_S)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--digest-only", action="store_true")
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level: <5} | {message}")
    logger.info(f"director start: interval={args.interval}s once={args.once} digest_only={args.digest_only}")
    if args.once or args.digest_only:
        asyncio.run(tick(digest_only=args.digest_only))
    else:
        asyncio.run(run(args.interval))


if __name__ == "__main__":
    main()
