"""Liquidation-cascade fade (liq_cascade_fade) v1 — shadow leg of the r2f forward test.

Round 2 (docs/wiki/signal-research-2026-10.md, "Round 2 (ticks)"), H8b-5m:
fading a 5-minute cascade made +26…+49 bps net in train (t_day 2.5–2.9) and
failed holdout (t ≤ 1.05), measured on a proxy (same-side aggressor bursts)
because no liquidation history exists. r2f re-tests it forward on REAL
liquidations recorded since 2026-10-09 (`ingestion.liquidation_recorder`,
`bybit_liquidations` + `bybit_liquidation_minutes`, migration 0044):
services/backtest/research/signal_2026_10_r2f/PREREG.md. The decision is that
harness evaluation at its fixed n; this module trades the 60-minute cell's
signals on the shadow wallet so the shadow tracker shows how live fills
compare with the band (strategy_configs.params.shadow_band).

The rule is `matrix_shared.liq_cascade` (shared with the r2f builder). Each
loop it evaluates every minute close tau whose entry bar [tau, tau + 1m) has
closed since the last loop: windows with a side >= $25k are candidates; Q
(trailing-7-day p99) is read from the recorder table, and only a candidate over
max(Q, $25k) costs a Bybit 1m-kline fetch for the displacement leg. A fire is
emitted once, from tau + 1m (round 2's entry: the close of the bar after the
window) until tau + 1m + `max_entry_delay_s`, for symbols the node streams
(traded universe + carry watchlist: they have tickers to fill against); fires
elsewhere are logged, not traded. Horizon 60 min, no TP/SL.

Stands down (no evaluation, no drafts, one log line per state change) when the
newest covered minute is older than `stale_after_s`: a silent feed must not
read as a quiet market.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from loguru import logger
from sqlalchemy import text

from matrix_shared import liq_cascade as LC
from matrix_shared import local_session_scope
from matrix_shared.markets.crypto import crypto_ingest_universe_async

from strategy.base import PredictionDraft

STRATEGY_ID = "liq_cascade_fade"
STRATEGY_VERSION = 1
DEFAULT_HORIZON_S = 60 * 60
DEFAULT_MAX_ENTRY_DELAY_S = 90
DEFAULT_STALE_AFTER_S = 180
_KLINES = "https://api.bybit.com/v5/market/kline"
_M = LC.MIN_MS

# module state across the 30 s loop (the dispatcher re-instantiates strategies every tick)
_state: dict = {"last_tau": None, "stale": None, "cov": set(), "cov_hi": None, "q": {}, "hour": None,
                "stats": {"candidates": 0, "over_q": 0, "fired": 0, "emitted": 0}}


def _log_feed(stale: bool, msg: str) -> None:
    if _state["stale"] != stale:
        _state["stale"] = stale
        logger.info(msg)


async def newest_covered_minute() -> datetime | None:
    async with local_session_scope() as s:
        return (await s.execute(text("SELECT max(minute) FROM bybit_liquidation_minutes"))).scalar()


async def load_coverage(since_ms: int) -> set[int]:
    async with local_session_scope() as s:
        rows = (await s.execute(text("SELECT minute FROM bybit_liquidation_minutes WHERE minute >= :t"),
                                {"t": datetime.fromtimestamp(since_ms / 1000, UTC)})).scalars().all()
    return {LC.to_ms(m) for m in rows}


async def load_liquidations(since_ms: int, until_ms: int, symbol: str | None = None) -> dict[str, dict]:
    """symbol -> minute ms -> [long usd, short usd] for ts in [since, until)."""
    q = "SELECT symbol, ts, side, notional_usd FROM bybit_liquidations WHERE ts >= :a AND ts < :b"
    args = {"a": datetime.fromtimestamp(since_ms / 1000, UTC), "b": datetime.fromtimestamp(until_ms / 1000, UTC)}
    if symbol:
        q += " AND symbol = :s"
        args["s"] = symbol
    async with local_session_scope() as s:
        rows = (await s.execute(text(q), args)).all()
    by: dict[str, list] = {}
    for sym, ts, side, usd in rows:
        by.setdefault(sym, []).append((LC.to_ms(ts), side, usd))
    return {sym: LC.bucket(r) for sym, r in by.items()}


async def fetch_bars(client: httpx.AsyncClient, symbol: str, end_ms: int) -> tuple[dict, dict]:
    """(closes, turnover) keyed by bar END ms for the 1m bars ending in
    (end - 24h - 15m, end], Bybit linear klines (the r2f evaluation's source)."""
    closes: dict[int, float] = {}
    turnover: dict[int, float] = {}
    lo = end_ms - LC.DAY_MS - 15 * _M
    hi = end_ms - _M  # start of the last bar
    while hi >= lo:
        r = await client.get(_KLINES, params={"category": "linear", "symbol": symbol, "interval": "1",
                                              "start": lo, "end": hi, "limit": 1000}, timeout=15)
        r.raise_for_status()
        rows = ((r.json().get("result") or {}).get("list")) or []
        if not rows:
            break
        for k in rows:  # newest first
            start = int(k[0])
            closes[start + _M] = float(k[4])
            turnover[start + _M] = float(k[6])
        hi = min(int(k[0]) for k in rows) - _M
    return closes, turnover


class LiqCascadeFade:
    id: str = STRATEGY_ID
    version: int = STRATEGY_VERSION
    market: str = "crypto"

    def __init__(
        self,
        symbols: Sequence[str] | None = None,  # the rule covers every USDT perp; drafts need a ticker
        *,
        horizon_s: int = DEFAULT_HORIZON_S,
        max_entry_delay_s: int = DEFAULT_MAX_ENTRY_DELAY_S,
        stale_after_s: int = DEFAULT_STALE_AFTER_S,
    ) -> None:
        self.symbols = list(symbols or [])
        self.horizon_s = horizon_s
        self.max_entry_delay_s = max_entry_delay_s
        self.stale_after_s = stale_after_s

    def feed_stale(self, newest: datetime | None, now: datetime) -> bool:
        """The newest covered minute ended more than stale_after_s ago (or none yet)."""
        return newest is None or (now - (newest + timedelta(minutes=1))).total_seconds() > self.stale_after_s

    def taus(self, last_tau: int | None, now_ms: int) -> list[int]:
        """Minute closes whose entry bar has closed since last_tau (first run: the latest only)."""
        hi = LC.minute_floor(now_ms) - LC.ENTRY_LAG_MIN * _M
        lo = hi if last_tau is None else max(last_tau + _M, hi - 30 * _M)
        return list(range(lo, hi + 1, _M))

    async def generate(self) -> list[PredictionDraft]:
        now = datetime.now(UTC)
        now_ms = LC.to_ms(now)
        newest = await newest_covered_minute()
        if self.feed_stale(newest, now):
            _state["last_tau"] = None  # resume from the present, never replay the gap
            _log_feed(True, f"{STRATEGY_ID}: liquidation feed stale (newest covered minute {newest}); standing down")
            return []
        _log_feed(False, f"{STRATEGY_ID}: liquidation feed live (newest covered minute {newest}); evaluating")

        taus = self.taus(_state["last_tau"], now_ms)
        if not taus:
            return []
        _state["last_tau"] = taus[-1]
        self._hourly(now_ms)

        # coverage: keep 7 days + 1 h in memory, add new minutes incrementally
        horizon = taus[0] - LC.LOOKBACK_MS - LC.H_MS
        since = horizon if _state["cov_hi"] is None else _state["cov_hi"] - 10 * _M
        _state["cov"] = {m for m in _state["cov"] if m >= horizon} | await load_coverage(since)
        _state["cov_hi"] = max(_state["cov"], default=None)
        cov = _state["cov"]

        recent = await load_liquidations(taus[0] - LC.WINDOW_MIN * _M, taus[-1])
        cands = [(sym, t) for sym, liq in recent.items() for t in LC.candidates(liq, cov, taus[0], taus[-1] + 1)]
        if not cands:
            return []
        tradable = set(await crypto_ingest_universe_async())
        drafts: list[PredictionDraft] = []
        async with httpx.AsyncClient() as client:
            for sym, tau in sorted(cands, key=lambda c: c[1]):
                _state["stats"]["candidates"] += 1
                d = await self._decide(client, sym, tau, recent[sym], cov)
                draft = self._act(sym, tau, d, now, sym in tradable)
                if draft is not None:
                    drafts.append(draft)
        return drafts

    async def _decide(self, client: httpx.AsyncClient, sym: str, tau: int, liq_recent: dict, cov: set) -> dict:
        hour = tau - tau % LC.H_MS
        key = (sym, hour)
        if key not in _state["q"]:
            hist = (await load_liquidations(hour - LC.LOOKBACK_MS - LC.WINDOW_MIN * _M, hour, sym)).get(sym, {})
            _state["q"][key] = LC.threshold(hist, cov, tau)
        q = _state["q"][key]
        lo, sh = LC.window_sums(liq_recent, tau)
        if q is not None and max(lo, sh) < max(q, LC.FLOOR_USD):
            return {"tau": tau, "side": None, "reason": "below p99", "L": lo, "S": sh, "Q": q}
        if q is None:
            return {"tau": tau, "side": None, "reason": "< 6 days covered", "L": lo, "S": sh, "Q": None}
        _state["stats"]["over_q"] += 1
        try:
            closes, turnover = await fetch_bars(client, sym, tau + LC.ENTRY_LAG_MIN * _M)
        except Exception as e:  # noqa: BLE001 — a failed fetch is a missed evaluation, logged
            return {"tau": tau, "side": None, "reason": f"kline fetch failed: {e}", "L": lo, "S": sh, "Q": q}
        d = LC.evaluate(tau, liq_recent, cov, closes, turnover, q=q)
        d["entry_px"] = closes.get(tau + LC.ENTRY_LAG_MIN * _M)
        return d

    def _act(self, sym: str, tau: int, d: dict, now: datetime, tradable: bool) -> PredictionDraft | None:
        t = datetime.fromtimestamp(tau / 1000, UTC).strftime("%H:%M")
        q = "n/a" if d.get("Q") is None else f"${d['Q']:,.0f}"
        head = f"{STRATEGY_ID}: {sym} {t} L ${d.get('L') or 0:,.0f} S ${d.get('S') or 0:,.0f} Q {q}"
        if d["side"] is None:
            logger.info(f"{head} -> no ({d['reason']})")
            return None
        _state["stats"]["fired"] += 1
        sig = f"r5 {d['r5'] * 100:+.2f}% (bar {max(LC.DISP_MIN, LC.DISP_SIGMA * d['sigma5']) * 100:.2f}%), 24h ${d['turnover'] / 1e6:,.1f}M"
        delay = (now - datetime.fromtimestamp((tau + LC.ENTRY_LAG_MIN * _M) / 1000, UTC)).total_seconds()
        if not tradable:
            logger.info(f"{head} {sig} -> FIRES {d['side'].upper()} (not streamed here: logged, not traded)")
            return None
        if delay > self.max_entry_delay_s:
            logger.info(f"{head} {sig} -> FIRES {d['side'].upper()} but entry window passed ({delay:.0f}s)")
            return None
        px = d.get("entry_px")
        if not px:
            logger.info(f"{head} {sig} -> FIRES {d['side'].upper()} but no entry bar close")
            return None
        _state["stats"]["emitted"] += 1
        logger.info(f"{head} {sig} -> {d['side'].upper()} {sym} {self.horizon_s // 60}m")
        return PredictionDraft(
            strategy_id=self.id,
            strategy_version=self.version,
            symbol=sym,
            exchange="bybit",
            side=d["side"],
            confidence=Decimal("0.5"),
            horizon_seconds=self.horizon_s,
            entry_price_ref=Decimal(str(px)),
            generated_at=now,
            thesis=(f"{sym} {'long' if d['side'] == 'long' else 'short'}-liquidation cascade: "
                    f"${max(d['L'], d['S']):,.0f} in 5 min (>= p99 ${d['Q']:,.0f}), {sig}; fade "
                    f"{self.horizon_s // 60} min (round 2 H8b on real liquidations, forward test r2f)"),
            context={
                "rule": f"r2f.L5_{self.horizon_s // 60}",
                "tau": datetime.fromtimestamp(tau / 1000, UTC).isoformat(),
                "liq_long_usd": d["L"], "liq_short_usd": d["S"], "q99_usd": d["Q"],
                "r5": d["r5"], "sigma5": d["sigma5"], "turnover_24h": d["turnover"],
                "cost_bps_r2f": LC.cost_bps(d["turnover"]),
            },
        )

    def _hourly(self, now_ms: int) -> None:
        hour = now_ms - now_ms % LC.H_MS
        if _state["hour"] is None:
            _state["hour"] = hour
        elif hour > _state["hour"]:
            st = _state["stats"]
            logger.info(f"{STRATEGY_ID}: last hour {st['candidates']} candidate windows >= ${LC.FLOOR_USD:,.0f}, "
                        f"{st['over_q']} over p99, {st['fired']} fired, {st['emitted']} emitted")
            _state["stats"] = {k: 0 for k in st}
            _state["hour"] = hour
            _state["q"] = {k: v for k, v in _state["q"].items() if k[1] >= hour - LC.H_MS}
