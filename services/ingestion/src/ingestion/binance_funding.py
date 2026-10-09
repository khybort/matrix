"""Binance USDⓈ-M funding poller — the second-venue feed for cross-exchange arb.

Polls Binance's PUBLIC futures `premiumIndex` endpoint (no auth, one call for
all symbols) every BINANCE_FUNDING_INTERVAL_S and writes a TickerSnapshot per
universe symbol tagged exchange="binance". The `xexch_funding_arb` strategy
differences this feed against the existing exchange="bybit" ticker feed.

MAINNET public data only — no keys, no orders (same rationale as the screener:
testnet funding is fake). Runs alongside ingestion via `ingestion.main`; gated
by BINANCE_FUNDING_ENABLED (default on when crypto is ingested).

Symbol naming: Binance USDT perps use the same `<BASE>USDT` string as Bybit
linear, so mapping is identity — only symbols present under that exact name on
BOTH venues are compared; name mismatches are simply skipped by the strategy.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import httpx
from loguru import logger
from sqlalchemy.dialects.postgresql import insert as pg_insert

from matrix_shared import session_scope  # local tier — where ticker snapshots live
from matrix_shared.markets.crypto import crypto_universe_async
from matrix_shared.models import TickerSnapshot

_PREMIUM_INDEX_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"
POLL_INTERVAL_S = float(os.environ.get("BINANCE_FUNDING_INTERVAL_S", "60"))
_EXCHANGE = "binance"


def _dec(v: object) -> Decimal | None:
    try:
        return Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


async def _poll_once(client: httpx.AsyncClient) -> int:
    """Fetch premiumIndex, keep universe symbols, batch-insert snapshots."""
    universe = set(await crypto_universe_async())
    if not universe:
        return 0

    resp = await client.get(_PREMIUM_INDEX_URL, timeout=10.0)
    resp.raise_for_status()
    rows = resp.json()
    if not isinstance(rows, list):
        logger.warning(f"binance funding: unexpected payload type {type(rows).__name__}")
        return 0

    now = datetime.now(UTC)
    values: list[dict] = []
    for r in rows:
        sym = r.get("symbol")
        if sym not in universe:
            continue
        fr = _dec(r.get("lastFundingRate"))
        if fr is None:
            continue
        nft = r.get("nextFundingTime")
        next_ts = (
            datetime.fromtimestamp(int(nft) / 1000, tz=UTC)
            if nft not in (None, 0)
            else None
        )
        mark = _dec(r.get("markPrice"))
        values.append({
            "id": uuid.uuid4(),
            "exchange": _EXCHANGE,
            "symbol": sym,
            "snapshot_ts": now,
            "last_price": mark,
            "mark_price": mark,
            "index_price": _dec(r.get("indexPrice")),
            "funding_rate": fr,
            "next_funding_ts": next_ts,
        })

    if not values:
        return 0
    async with session_scope() as session:
        await session.execute(pg_insert(TickerSnapshot).values(values))
    return len(values)


async def run() -> None:
    if os.environ.get("BINANCE_FUNDING_ENABLED", "true").strip().lower() == "false":
        logger.info("binance funding poller disabled (BINANCE_FUNDING_ENABLED=false)")
        return
    logger.info(
        f"binance funding poller start: interval={POLL_INTERVAL_S}s url={_PREMIUM_INDEX_URL}"
    )
    async with httpx.AsyncClient() as client:
        while True:
            try:
                n = await _poll_once(client)
                logger.info(f"binance funding: wrote {n} snapshot(s)")
            except httpx.HTTPError as e:
                logger.warning(f"binance funding poll HTTP error: {e}")
            except Exception as e:  # noqa: BLE001 — never let one bad poll kill the loop
                logger.warning(f"binance funding poll failed: {e}")
            await asyncio.sleep(POLL_INTERVAL_S)
