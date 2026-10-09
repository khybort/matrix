"""Dynamic US equity symbol discovery — no hardcoded ticker lists.

Builds the tradeable universe from public index-constituent tables
(S&P 500 + Nasdaq-100 on Wikipedia). Results upsert into `us_symbols`;
symbols missing from the latest fetch are deactivated (not deleted).

Sources are selectable via `US_UNIVERSE_SOURCES` (default "sp500,nasdaq100").
For dev/CI without network scraping, `US_SYMBOLS` (comma-separated) seeds a
manual universe instead.
"""

from __future__ import annotations

import io
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
import pandas as pd
from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from matrix_shared import session_scope
from matrix_shared.models import UsSymbol

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
NASDAQ100_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"
_USER_AGENT = os.environ.get("US_DISCOVER_USER_AGENT", "Matrix-Ingestion/1.0")

# US tickers: 1–5 uppercase letters, optional single-letter class share (BRK-B).
_SYMBOL_RE = re.compile(r"^[A-Z]{1,5}(-[A-Z])?$")


@dataclass(slots=True)
class DiscoveredSymbol:
    symbol: str
    name: str | None = None
    sector: str | None = None
    exchange: str | None = None
    index_membership: set[str] = field(default_factory=set)


def _normalize_ticker(raw: str) -> str | None:
    """yfinance uses '-' for class shares (BRK-B), Wikipedia uses '.' (BRK.B)."""
    s = str(raw).strip().upper().replace(".", "-")
    return s if _SYMBOL_RE.match(s) else None


def _read_tables(html: str) -> list[pd.DataFrame]:
    return pd.read_html(io.StringIO(html))


def _pick_column(df: pd.DataFrame, *candidates: str) -> str | None:
    cols = {str(c).strip().lower(): c for c in df.columns}
    for cand in candidates:
        if cand in cols:
            return cols[cand]
    return None


def _parse_sp500(html: str) -> list[DiscoveredSymbol]:
    out: list[DiscoveredSymbol] = []
    for df in _read_tables(html):
        sym_col = _pick_column(df, "symbol", "ticker")
        if sym_col is None:
            continue
        name_col = _pick_column(df, "security", "company")
        sec_col = _pick_column(df, "gics sector", "sector")
        for _, row in df.iterrows():
            sym = _normalize_ticker(row[sym_col])
            if not sym:
                continue
            out.append(
                DiscoveredSymbol(
                    symbol=sym,
                    name=str(row[name_col]).strip() if name_col else None,
                    sector=str(row[sec_col]).strip() if sec_col else None,
                    exchange=None,
                    index_membership={"SP500"},
                )
            )
        if out:
            break  # first matching table is the constituents list
    return out


def _parse_nasdaq100(html: str) -> list[DiscoveredSymbol]:
    out: list[DiscoveredSymbol] = []
    for df in _read_tables(html):
        sym_col = _pick_column(df, "ticker", "symbol")
        name_col = _pick_column(df, "company", "security")
        if sym_col is None or name_col is None:
            continue
        rows: list[DiscoveredSymbol] = []
        for _, row in df.iterrows():
            sym = _normalize_ticker(row[sym_col])
            if not sym:
                continue
            sec_col = _pick_column(df, "gics sector", "sector")
            rows.append(
                DiscoveredSymbol(
                    symbol=sym,
                    name=str(row[name_col]).strip(),
                    sector=str(row[sec_col]).strip() if sec_col else None,
                    exchange="NASDAQ",
                    index_membership={"NDX"},
                )
            )
        # The components table is the one with many tickers (>50).
        if len(rows) > 50:
            return rows
    return out


async def _fetch_html(url: str, client: httpx.AsyncClient) -> str:
    resp = await client.get(url, headers={"User-Agent": _USER_AGENT})
    resp.raise_for_status()
    return resp.text


def _merge(*groups: list[DiscoveredSymbol]) -> list[DiscoveredSymbol]:
    by_symbol: dict[str, DiscoveredSymbol] = {}
    for group in groups:
        for s in group:
            cur = by_symbol.get(s.symbol)
            if cur is None:
                by_symbol[s.symbol] = s
                continue
            cur.index_membership |= s.index_membership
            cur.name = cur.name or s.name
            cur.sector = cur.sector or s.sector
            cur.exchange = cur.exchange or s.exchange
    return sorted(by_symbol.values(), key=lambda d: d.symbol)


def _manual_override() -> list[DiscoveredSymbol] | None:
    env = os.environ.get("US_SYMBOLS", "").strip()
    if not env:
        return None
    syms = [_normalize_ticker(s) for s in env.split(",")]
    out = [DiscoveredSymbol(symbol=s, index_membership={"MANUAL"}) for s in syms if s]
    logger.info(f"US discover: using US_SYMBOLS override ({len(out)} symbols)")
    return out


async def fetch_discovered_symbols(
    *, client: httpx.AsyncClient | None = None
) -> list[DiscoveredSymbol]:
    """Pull the candidate US universe from the configured index sources."""
    manual = _manual_override()
    if manual is not None:
        return manual

    sources = {
        s.strip().lower()
        for s in os.environ.get("US_UNIVERSE_SOURCES", "sp500,nasdaq100").split(",")
        if s.strip()
    }
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=True)
    groups: list[list[DiscoveredSymbol]] = []
    try:
        if "sp500" in sources:
            groups.append(_parse_sp500(await _fetch_html(SP500_URL, client)))
        if "nasdaq100" in sources:
            groups.append(_parse_nasdaq100(await _fetch_html(NASDAQ100_URL, client)))
    finally:
        if owns_client:
            await client.aclose()

    merged = _merge(*groups)
    if not merged:
        raise RuntimeError(f"US discover returned 0 symbols from sources={sorted(sources)}")
    logger.info(
        f"US discover: {len(merged)} symbols from {sorted(sources)} "
        f"(sp500+ndx merged, class shares normalized)"
    )
    return merged


async def refresh_universe(*, bootstrap_active: bool | None = None) -> int:
    """Upsert discovered symbols and deactivate delisted tickers.

    bootstrap_active:
      - None (default): if `us_symbols` is empty → activate all discovered;
        otherwise new symbols start inactive (universe manager promotes winners).
      - True/False: force initial active flag on insert.
    """
    discovered = await fetch_discovered_symbols()
    now = datetime.now(UTC)
    discovered_codes = {s.symbol for s in discovered}

    async with session_scope() as session:
        existing = (await session.execute(select(UsSymbol.symbol))).all()
        table_empty = len(existing) == 0

        rows = []
        for s in discovered:
            if bootstrap_active is not None:
                active = bootstrap_active
            elif table_empty:
                active = True
            else:
                active = False  # universe manager promotes new probes
            rows.append(
                {
                    "symbol": s.symbol,
                    "name": s.name,
                    "sector": s.sector,
                    "exchange": s.exchange,
                    "index_membership": ",".join(sorted(s.index_membership)) or None,
                    "active": active,
                    "last_refreshed_at": now,
                }
            )

        stmt = pg_insert(UsSymbol).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["symbol"],
            set_={
                "name": stmt.excluded.name,
                "sector": stmt.excluded.sector,
                "exchange": stmt.excluded.exchange,
                "index_membership": stmt.excluded.index_membership,
                "last_refreshed_at": stmt.excluded.last_refreshed_at,
                # Never overwrite `active` on update — universe manager owns that.
            },
        )
        result = await session.execute(stmt)
        upserted = result.rowcount or len(rows)

        if discovered_codes:
            await session.execute(
                update(UsSymbol)
                .where(UsSymbol.symbol.notin_(discovered_codes))
                .values(active=False, last_refreshed_at=now)
            )
            if bootstrap_active is True:
                await session.execute(
                    update(UsSymbol)
                    .where(UsSymbol.symbol.in_(discovered_codes))
                    .values(active=True, last_refreshed_at=now)
                )

    logger.info(f"US discover: upserted {upserted} symbols (table_empty={table_empty})")
    return upserted
