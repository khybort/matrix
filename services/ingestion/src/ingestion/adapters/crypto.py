"""Crypto IngestorAdapter — Bybit perp stream plus the carry spot legs.

The perp stream follows `crypto_ingest_universe_async()`: the traded universe
(`tradable_symbols` asset_class=crypto) plus the carry watchlist
(asset_class=crypto_carry, refreshed hourly by ingestion.carry_watchlist).
Every UNIVERSE_RECONCILE_INTERVAL_S (default 300 s), and right after each
watchlist refresh, the set is re-read and the diff is (un)subscribed on the
live socket — no reconnect, no gap for the symbols that stay.

On mainnet each watchlist coin's spot leg streams too (ticker every 15 s,
closed 1m candles, top-of-book snapshot every 10 s), from Bybit spot or
Binance spot as the watchlist row names.
An explicit symbol list (CLI / CRYPTO_SYMBOLS) streams exactly that list.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable

from loguru import logger
from sqlalchemy import text

from matrix_shared import shared_session_scope
from matrix_shared.markets import IngestorAdapter
from matrix_shared.markets.crypto import (
    CARRY_WATCHLIST_ASSET_CLASS,
    crypto_ingest_universe_async,
)

from ingestion import carry_watchlist
from ingestion.connectors.binance_spot import BinanceSpotConnector
from ingestion.connectors.bybit import BybitConnector
from ingestion.persist import persist_events

RECONCILE_INTERVAL_S = float(
    os.environ.get("UNIVERSE_RECONCILE_INTERVAL_S", "300")
)
SPOT_TICKER_INTERVAL_S = float(os.environ.get("CARRY_SPOT_TICKER_INTERVAL_S", "15"))
SPOT_BOOK_INTERVAL_S = float(os.environ.get("CARRY_SPOT_BOOK_INTERVAL_S", "10"))


async def _default_symbols() -> list[str]:
    # Async so the reconcile reads the live sets (the sync path returns []
    # under a running loop). Override via CRYPTO_SYMBOLS.
    return await crypto_ingest_universe_async()


async def spot_legs() -> dict[str, list[str]]:
    """venue -> spot pairs of the active carry watchlist."""
    async with shared_session_scope() as db:
        rows = (await db.execute(text(
            "SELECT components_json->>'spot_venue', components_json->>'spot_symbol' "
            "FROM tradable_symbols WHERE asset_class = :ac AND active "
            "AND components_json->>'spot_symbol' IS NOT NULL ORDER BY rank NULLS LAST"),
            {"ac": CARRY_WATCHLIST_ASSET_CLASS})).all()
    out: dict[str, list[str]] = {"bybit": [], "binance": []}
    for venue, pair in rows:
        if venue in out and pair not in out[venue]:
            out[venue].append(pair)
    return out


class CryptoIngestor(IngestorAdapter):
    def __init__(
        self,
        symbols: list[str] | None = None,
        *,
        testnet: bool | None = None,
    ) -> None:
        self._init_symbols = list(symbols) if symbols else None
        if testnet is None:
            testnet = (
                os.environ.get("BYBIT_TESTNET", "true").strip().lower() != "false"
            )
        self.testnet = testnet

    async def run(self) -> None:
        dynamic = self._init_symbols is None
        perp = BybitConnector(self._init_symbols or await _default_symbols(), testnet=self.testnet)
        logger.info(f"[crypto] ingest start: symbols={perp.symbols} testnet={self.testnet}")
        streams: dict[str, Callable[[], object]] = {"perp": perp.stream}
        bybit_spot: BybitConnector | None = None
        binance_spot: BinanceSpotConnector | None = None
        carry = dynamic and not self.testnet and carry_watchlist.ENABLED
        changed = asyncio.Event()
        if carry:
            bybit_spot = BybitConnector(
                [], testnet=False, category="spot",
                ticker_interval_s=SPOT_TICKER_INTERVAL_S, book_interval_s=SPOT_BOOK_INTERVAL_S,
            )
            binance_spot = BinanceSpotConnector()
            streams["bybit-spot"] = bybit_spot.stream
            streams["binance-spot"] = binance_spot.stream

        tasks: dict[str, asyncio.Task] = {}

        def _ensure_tasks() -> None:
            for name, make in streams.items():
                t = tasks.get(name)
                if t is not None and not t.done():
                    continue
                if t is not None:
                    exc = t.exception() if not t.cancelled() else None
                    logger.warning(f"[crypto] {name} stream ended ({exc}); restarting")
                tasks[name] = asyncio.create_task(persist_events(make()), name=f"crypto_{name}")

        async def _reconcile() -> None:
            await perp.set_symbols(await _default_symbols())
            if carry and bybit_spot is not None and binance_spot is not None:
                legs = await spot_legs()
                await bybit_spot.set_symbols(legs["bybit"])
                await binance_spot.set_symbols(legs["binance"])

        aux: list[asyncio.Task] = []
        if carry:
            aux.append(asyncio.create_task(carry_watchlist.run(changed), name="carry_watchlist"))
        loop = asyncio.get_running_loop()
        # Spot legs resume from the stored watchlist before the first refresh lands.
        last_reconcile = loop.time() - RECONCILE_INTERVAL_S if carry else loop.time()
        try:
            while True:
                _ensure_tasks()  # a stream that died restarts within 10 s
                due = loop.time() - last_reconcile >= RECONCILE_INTERVAL_S
                if dynamic and (due or changed.is_set()):
                    changed.clear()
                    last_reconcile = loop.time()
                    try:
                        await _reconcile()
                    except Exception as e:  # noqa: BLE001 — keep streaming the current set
                        logger.warning(f"[crypto] universe reconcile failed: {e}")
                try:
                    await asyncio.wait_for(changed.wait(), timeout=10.0)
                except TimeoutError:
                    pass
        finally:
            for t in [*tasks.values(), *aux]:
                t.cancel()
            await asyncio.gather(*tasks.values(), *aux, return_exceptions=True)
