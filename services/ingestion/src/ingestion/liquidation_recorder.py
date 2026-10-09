"""Bybit liquidation recorder: the input of the r2f forward test.

Round 2's H8b ("fade a 5-minute liquidation cascade") passed train and failed
holdout on a proxy (same-side aggressor bursts), because no venue serves
historical liquidations. This records the real thing, going forward: Bybit's
public `allLiquidation.<symbol>` for EVERY trading USDT perp (measured
2026-10-09: 791 perps on two sockets, a few thousand events a day), through the
same `BybitConnector` the perp stream uses, restricted to that one topic.

Writes (local tier, migration 0044):
  * `bybit_liquidations`: one row per event (side = the position liquidated);
  * `bybit_liquidation_minutes`: one row per UTC minute during which every
    socket had been subscribed since before the minute started and was still
    receiving frames when it ended, written only after that minute's events
    were flushed. A minute without a row is a minute the feed cannot vouch for:
    the r2f rule (matrix_shared.liq_cascade) treats it as unknown, and the
    shadow module `liq_cascade_fade` stands down when the newest row is old.

The universe is re-read hourly from `/v5/market/instruments-info`; new perps join
the socket with the most room (or a new one) without bouncing the others. Rows
older than RETENTION_DAYS are deleted hourly through the ts / minute indexes.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import UTC, datetime, timedelta

import httpx
from loguru import logger
from sqlalchemy import text

from matrix_shared import local_session_scope

from ingestion.connectors.bybit import BybitConnector, LiquidationEvent

ENABLED = os.environ.get("LIQ_RECORDER_ENABLED", "true").strip().lower() != "false"
SHARD_SIZE = int(os.environ.get("LIQ_RECORDER_SHARD_SIZE", "400"))  # topics per socket
UNIVERSE_REFRESH_S = 3600.0
RETENTION_DAYS = float(os.environ.get("LIQ_RECORDER_RETENTION_DAYS", "180"))
PRUNE_EVERY_S = 3600.0
FLUSH_S = 1.0
TOPIC = "allLiquidation"
_INSTRUMENTS = "https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000"


def usdt_perps(pages: list[dict]) -> list[str]:
    """Trading USDT linear perpetuals from instruments-info pages."""
    out: list[str] = []
    for p in pages:
        for i in ((p.get("result") or {}).get("list")) or []:
            if (i.get("status") == "Trading" and i.get("quoteCoin") == "USDT"
                    and i.get("contractType") == "LinearPerpetual"):
                out.append(i["symbol"])
    return sorted(set(out))


async def fetch_perps(client: httpx.AsyncClient) -> list[str]:
    pages, cursor = [], ""
    for _ in range(10):
        r = await client.get(_INSTRUMENTS + (f"&cursor={cursor}" if cursor else ""), timeout=20)
        r.raise_for_status()
        j = r.json()
        if j.get("retCode", 0) != 0:
            raise RuntimeError(f"instruments-info retCode {j.get('retCode')} {j.get('retMsg')}")
        pages.append(j)
        cursor = (j.get("result") or {}).get("nextPageCursor") or ""
        if not cursor:
            break
    return usdt_perps(pages)


def assign(shards: list[list[str]], symbols: list[str], size: int) -> tuple[list[list[str]], int]:
    """New shard lists for `symbols`: kept symbols stay where they are, removed ones
    leave, added ones fill the emptiest shard below `size`, else open a new one.
    Returns (shards, number of shards that changed)."""
    want = set(symbols)
    new = [[s for s in sh if s in want] for sh in shards]
    have = {s for sh in new for s in sh}
    for s in symbols:
        if s in have:
            continue
        room = [i for i, sh in enumerate(new) if len(sh) < size]
        if room:
            new[min(room, key=lambda i: len(new[i]))].append(s)
        else:
            new.append([s])
        have.add(s)
    changed = sum(1 for i, sh in enumerate(new) if i >= len(shards) or sh != shards[i])
    return new, changed


def covered_minute(minute_start_s: float, conns: list[BybitConnector]) -> bool:
    """Every socket subscribed before the minute began and still live now."""
    return bool(conns) and all(
        c.is_up() and c.session_started is not None and c.session_started <= minute_start_s for c in conns
    )


class Recorder:
    def __init__(self) -> None:
        self.conns: list[BybitConnector] = []
        self.tasks: list[asyncio.Task] = []
        self.buf: list[LiquidationEvent] = []
        self.minute_events: dict[int, int] = {}  # minute start s -> events received
        self.written = 0

    def _start(self, conn: BybitConnector) -> None:
        async def pump() -> None:
            async for ev in conn.stream():
                if isinstance(ev, LiquidationEvent):
                    self.buf.append(ev)
        self.conns.append(conn)
        self.tasks.append(asyncio.create_task(pump(), name=f"liq-shard-{len(self.conns)}"))

    async def set_universe(self, symbols: list[str]) -> None:
        shards, changed = assign([list(c.symbols) for c in self.conns], symbols, SHARD_SIZE)
        for i, sh in enumerate(shards):
            if i < len(self.conns):
                if sh != self.conns[i].symbols:
                    await self.conns[i].set_symbols(sh)
            else:
                self._start(BybitConnector(sh, testnet=False, topics=(TOPIC,)))
        if changed:
            logger.info(f"liquidation recorder: {len(symbols)} USDT perps on {len(self.conns)} sockets")

    async def flush(self) -> None:
        if not self.buf:
            return
        batch, self.buf = self.buf, []
        try:
            async with local_session_scope() as s:
                await s.execute(
                    text("INSERT INTO bybit_liquidations (symbol, ts, side, size, price, notional_usd) "
                         "VALUES (:symbol, :ts, :side, :size, :price, :usd)"),
                    [{"symbol": e.symbol, "ts": e.ts, "side": e.side, "size": e.size, "price": e.price,
                      "usd": float(e.size * e.price)} for e in batch],
                )
        except Exception:
            self.buf[:0] = batch  # keep them; the minute is not marked covered until they land
            raise
        self.written += len(batch)
        for e in batch:
            m = int(e.ts.timestamp()) // 60 * 60
            self.minute_events[m] = self.minute_events.get(m, 0) + 1

    async def mark(self, minute_start_s: int) -> bool:
        """Write the coverage row for the minute that just ended, if the feed vouches for it."""
        n = self.minute_events.pop(minute_start_s, 0)
        for k in [k for k in self.minute_events if k < minute_start_s - 600]:
            del self.minute_events[k]
        if not covered_minute(minute_start_s, self.conns):
            return False
        async with local_session_scope() as s:
            await s.execute(
                text("INSERT INTO bybit_liquidation_minutes (minute, n_symbols, n_events) "
                     "VALUES (:m, :ns, :ne) ON CONFLICT DO NOTHING"),
                {"m": datetime.fromtimestamp(minute_start_s, UTC),
                 "ns": sum(len(c.symbols) for c in self.conns), "ne": n},
            )
        return True


async def prune() -> tuple[int, int]:
    cutoff = datetime.now(UTC) - timedelta(days=RETENTION_DAYS)
    async with local_session_scope() as s:
        a = await s.execute(text("DELETE FROM bybit_liquidations WHERE ts < :cut"), {"cut": cutoff})
        b = await s.execute(text("DELETE FROM bybit_liquidation_minutes WHERE minute < :cut"), {"cut": cutoff})
    return a.rowcount or 0, b.rowcount or 0


async def run() -> None:
    if not ENABLED:
        logger.info("liquidation recorder disabled (LIQ_RECORDER_ENABLED=false)")
        return
    rec = Recorder()
    refreshed_at = pruned_at = -1e9
    hour_written = hour_marked = 0
    last_minute = int(time.time()) // 60 * 60
    async with httpx.AsyncClient() as client:
        try:
            while True:
                now = time.monotonic()
                if now - refreshed_at >= UNIVERSE_REFRESH_S:
                    try:
                        await rec.set_universe(await fetch_perps(client))
                        refreshed_at = now
                    except Exception as e:  # noqa: BLE001 — keep the current set, retry in a minute
                        logger.warning(f"liquidation recorder: universe refresh failed: {e}")
                        refreshed_at = now - UNIVERSE_REFRESH_S + 60
                for t in rec.tasks:
                    if t.done() and not t.cancelled() and t.exception():
                        logger.warning(f"liquidation recorder: shard task died: {t.exception()}")
                try:
                    before = rec.written
                    await rec.flush()
                    hour_written += rec.written - before
                    cur = int(time.time()) // 60 * 60
                    if cur > last_minute:
                        # minutes [last_minute, cur) ended; only the one just ended can be vouched for
                        if await rec.mark(cur - 60):
                            hour_marked += 1
                        last_minute = cur
                        if cur % 3600 == 0:
                            logger.info(f"liquidation recorder: last hour {hour_written} events, "
                                        f"{hour_marked}/60 minutes covered, {sum(len(c.symbols) for c in rec.conns)} perps")
                            hour_written = hour_marked = 0
                except Exception as e:  # noqa: BLE001 — never let one bad flush kill the loop
                    logger.warning(f"liquidation recorder flush failed: {e}")
                if now - pruned_at >= PRUNE_EVERY_S:
                    try:
                        a, b = await prune()
                        pruned_at = now
                        if a or b:
                            logger.info(f"liquidation recorder: pruned {a} events, {b} minutes older than {RETENTION_DAYS:g}d")
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"liquidation recorder prune failed: {e}")
                        pruned_at = now
                await asyncio.sleep(FLUSH_S)
        finally:
            for t in rec.tasks:
                t.cancel()
            await asyncio.gather(*rec.tasks, return_exceptions=True)
