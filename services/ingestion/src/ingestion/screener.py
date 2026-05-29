"""Deterministic opportunity screener — no LLM, pure REST + statistics.

Polls Bybit's public tickers endpoint for ALL linear USDT perpetuals every
POLL_INTERVAL_S seconds and surfaces symbols whose funding rate or OI delta
exceed statistical thresholds. Results are written to the `screener_signals`
table (see below) for the dashboard and strategy service to consume.

Design principles (CLAUDE.md):
  - Deterministic first: ranking and thresholds are pure arithmetic.
  - LLM only for confirmed anomalies: if a symbol passes threshold for
    CONFIRM_PASSES consecutive polls AND appears in recent news (raw_documents),
    the caller MAY invoke LLM once to judge catalyst vs. noise. The screener
    itself never calls LLM.
  - Operator-in-the-loop for watchlist promotion: passing the screener writes
    a signal row (status='candidate'); it does NOT automatically add the symbol
    to CRYPTO_SYMBOLS. Operator promotes via dashboard or `make screener-promote`.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import httpx
from loguru import logger
from matrix_shared import session_scope
from sqlalchemy import text

# ── Bybit REST (public, no auth) ─────────────────────────────────────────────
_TESTNET = os.environ.get("BYBIT_TESTNET", "true").strip().lower() != "false"
_BASE = (
    "https://api-testnet.bybit.com" if _TESTNET else "https://api.bybit.com"
)
_TICKERS_URL = f"{_BASE}/v5/market/tickers"

# ── Thresholds ────────────────────────────────────────────────────────────────
# Funding rate: |fr| must exceed this to surface as a reversion candidate.
FUNDING_THRESHOLD = Decimal(os.environ.get("SCREENER_FUNDING_THRESHOLD", "0.0003"))
# OI delta: percent change in open_interest_value over last two polls.
OI_DELTA_THRESHOLD = Decimal(os.environ.get("SCREENER_OI_DELTA_THRESHOLD", "0.03"))
# A symbol needs this many consecutive qualifying polls before it becomes a
# 'candidate' (status promoted from 'signal' → 'candidate' in screener_signals).
CONFIRM_PASSES = int(os.environ.get("SCREENER_CONFIRM_PASSES", "3"))
# Top-N by absolute funding rate included in each poll regardless of threshold.
TOP_N_FUNDING = int(os.environ.get("SCREENER_TOP_N_FUNDING", "10"))

POLL_INTERVAL_S = float(os.environ.get("SCREENER_POLL_INTERVAL_S", "300"))  # 5 min


@dataclass(slots=True)
class ScreenerRow:
    symbol: str
    funding_rate: Decimal
    open_interest_value: Decimal | None
    signal_type: str   # "funding_extreme" | "oi_spike" | "top_funding"
    score: Decimal     # |funding_rate| or oi_delta_pct


async def fetch_all_tickers() -> list[dict]:
    """GET /v5/market/tickers?category=linear — returns all USDT-perp tickers."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(_TICKERS_URL, params={"category": "linear"})
        r.raise_for_status()
    data = r.json()
    if data.get("retCode") != 0:
        raise RuntimeError(f"Bybit tickers error: {data.get('retMsg')}")
    return data["result"]["list"]


def rank_tickers(
    tickers: list[dict],
    prev_oi: dict[str, Decimal],
) -> list[ScreenerRow]:
    """Pure deterministic ranking — no LLM, no DB reads."""
    rows: list[ScreenerRow] = []
    fr_ranked: list[tuple[Decimal, dict]] = []

    for t in tickers:
        symbol: str = t.get("symbol", "")
        if not symbol.endswith("USDT"):
            continue
        try:
            fr = Decimal(str(t.get("fundingRate") or "0"))
        except Exception:
            continue

        fr_ranked.append((abs(fr), t))

        # Funding extreme signal
        if abs(fr) >= FUNDING_THRESHOLD:
            rows.append(ScreenerRow(
                symbol=symbol,
                funding_rate=fr,
                open_interest_value=_dec(t, "openInterestValue"),
                signal_type="funding_extreme",
                score=abs(fr),
            ))

        # OI delta signal
        oi = _dec(t, "openInterestValue")
        if oi is not None and symbol in prev_oi and prev_oi[symbol] > 0:
            delta = (oi - prev_oi[symbol]) / prev_oi[symbol]
            if abs(delta) >= OI_DELTA_THRESHOLD:
                rows.append(ScreenerRow(
                    symbol=symbol,
                    funding_rate=fr,
                    open_interest_value=oi,
                    signal_type="oi_spike",
                    score=abs(delta),
                ))

    # Always include top-N by |funding_rate| (even below threshold) so
    # operators see the full funding landscape in the dashboard.
    fr_ranked.sort(key=lambda x: x[0], reverse=True)
    for _, t in fr_ranked[:TOP_N_FUNDING]:
        symbol = t["symbol"]
        if not any(r.symbol == symbol and r.signal_type == "funding_extreme" for r in rows):
            rows.append(ScreenerRow(
                symbol=symbol,
                funding_rate=Decimal(str(t.get("fundingRate") or "0")),
                open_interest_value=_dec(t, "openInterestValue"),
                signal_type="top_funding",
                score=abs(Decimal(str(t.get("fundingRate") or "0"))),
            ))

    return rows


def _dec(t: dict, key: str) -> Decimal | None:
    v = t.get(key)
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except Exception:
        return None


def _f(t: dict, key: str) -> float | None:
    v = t.get(key)
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


async def _upsert_universe_snapshot(tickers: list[dict]) -> None:
    """Persist exchange-wide ticker stats for the universe manager.

    One row per USDT-perp symbol, latest poll wins. The /v5/market/tickers
    payload already carries turnover/volume/24h-change/high/low/last for every
    symbol — the screener only needed funding/OI for signals, but the universe
    scorer needs these to rank liquidity/volatility/momentum WITHOUT ingesting
    bars (solves the bootstrap chicken-and-egg). LOCAL tier, no risk impact.
    """
    now = datetime.now(UTC)
    params: list[dict] = []
    for t in tickers:
        symbol = t.get("symbol", "")
        if not symbol.endswith("USDT"):
            continue
        params.append({
            "symbol": symbol,
            "turnover24h": _f(t, "turnover24h"),
            "volume24h": _f(t, "volume24h"),
            "price24h_pct": _f(t, "price24hPcnt"),
            "high24h": _f(t, "highPrice24h"),
            "low24h": _f(t, "lowPrice24h"),
            "last_price": _f(t, "lastPrice"),
            "oi_value": _f(t, "openInterestValue"),
            "funding_rate": _f(t, "fundingRate"),
            "observed_at": now,
        })
    if not params:
        return
    async with session_scope() as db:
        await db.execute(text("""
            INSERT INTO screener_universe_snapshot
              (symbol, turnover24h, volume24h, price24h_pct, high24h, low24h,
               last_price, oi_value, funding_rate, observed_at)
            VALUES
              (:symbol, :turnover24h, :volume24h, :price24h_pct, :high24h, :low24h,
               :last_price, :oi_value, :funding_rate, :observed_at)
            ON CONFLICT (symbol) DO UPDATE SET
              turnover24h  = EXCLUDED.turnover24h,
              volume24h    = EXCLUDED.volume24h,
              price24h_pct = EXCLUDED.price24h_pct,
              high24h      = EXCLUDED.high24h,
              low24h       = EXCLUDED.low24h,
              last_price   = EXCLUDED.last_price,
              oi_value     = EXCLUDED.oi_value,
              funding_rate = EXCLUDED.funding_rate,
              observed_at  = EXCLUDED.observed_at
        """), params)


async def _upsert_signals(rows: list[ScreenerRow], pass_counts: dict[str, int]) -> None:
    """Write screener signals to LOCAL postgres (analytics only — no risk impact)."""
    now = datetime.now(UTC)
    async with session_scope() as db:
        for r in rows:
            passes = pass_counts.get(r.symbol, 1)
            status = "candidate" if passes >= CONFIRM_PASSES else "signal"
            await db.execute(text("""
                INSERT INTO screener_signals
                  (symbol, signal_type, funding_rate, oi_value, score, passes, status, observed_at)
                VALUES
                  (:symbol, :signal_type, :funding_rate, :oi_value, :score, :passes, :status, :observed_at)
                ON CONFLICT (symbol, signal_type)
                DO UPDATE SET
                  funding_rate = EXCLUDED.funding_rate,
                  oi_value     = EXCLUDED.oi_value,
                  score        = EXCLUDED.score,
                  passes       = EXCLUDED.passes,
                  status       = EXCLUDED.status,
                  observed_at  = EXCLUDED.observed_at
            """), {
                "symbol": r.symbol,
                "signal_type": r.signal_type,
                "funding_rate": float(r.funding_rate),
                "oi_value": float(r.open_interest_value) if r.open_interest_value else None,
                "score": float(r.score),
                "passes": passes,
                "status": status,
                "observed_at": now,
            })


async def run_once(prev_oi: dict[str, Decimal]) -> dict[str, Decimal]:
    """One screener poll. Returns updated prev_oi for next call."""
    try:
        tickers = await fetch_all_tickers()
    except Exception as e:
        logger.warning(f"screener: ticker fetch failed: {e}")
        return prev_oi

    rows = rank_tickers(tickers, prev_oi)

    # Persist exchange-wide stats for the universe manager (best-effort; never
    # crash ingestion if the table isn't migrated yet).
    try:
        await _upsert_universe_snapshot(tickers)
    except Exception as e:
        logger.debug(f"screener: universe snapshot upsert failed (table missing?): {e}")

    # Update prev_oi from this poll
    new_oi: dict[str, Decimal] = {}
    for t in tickers:
        oi = _dec(t, "openInterestValue")
        if oi is not None:
            new_oi[t["symbol"]] = oi

    if rows:
        # Count consecutive passes per symbol (simple in-memory counter)
        pass_counts: dict[str, int] = {}
        for r in rows:
            pass_counts[r.symbol] = pass_counts.get(r.symbol, 0) + 1

        try:
            await _upsert_signals(rows, pass_counts)
        except Exception as e:
            # Table may not exist yet — log and continue, never crash ingestion
            logger.debug(f"screener: signal upsert failed (table missing?): {e}")

        candidates = [r for r in rows if r.signal_type != "top_funding"]
        if candidates:
            top = sorted(candidates, key=lambda r: r.score, reverse=True)[:5]
            logger.info(
                "screener: " +
                " | ".join(
                    f"{r.symbol} {r.signal_type} score={float(r.score):.4f}"
                    for r in top
                )
            )

    return new_oi


async def run(interval_s: float = POLL_INTERVAL_S) -> None:
    prev_oi: dict[str, Decimal] = {}
    while True:
        t0 = time.monotonic()
        prev_oi = await run_once(prev_oi)
        elapsed = time.monotonic() - t0
        await asyncio.sleep(max(0.0, interval_s - elapsed))


import asyncio  # noqa: E402 (placed here to avoid circular at module top)
