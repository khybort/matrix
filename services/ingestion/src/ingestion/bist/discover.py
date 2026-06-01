"""Dynamic BIST symbol discovery — no hardcoded ticker lists.

Fetches the tradeable equity universe from a configurable HTTP JSON endpoint
(default: Fintables public companies API). Results upsert into `bist_symbols`;
symbols missing from the latest fetch are deactivated (not deleted).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from matrix_shared import session_scope
from matrix_shared.models import BistSymbol

DEFAULT_DISCOVER_URL = "https://api.fintables.com/companies/?format=json"
_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9]{2,9}$")


@dataclass(frozen=True, slots=True)
class DiscoveredSymbol:
    symbol: str
    name: str | None = None
    sector: str | None = None
    index_membership: str | None = None


def _parse_fintables_payload(data: object) -> list[DiscoveredSymbol]:
    if not isinstance(data, list):
        raise ValueError("discover payload must be a JSON array")
    out: list[DiscoveredSymbol] = []
    seen: set[str] = set()
    for row in data:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or "").strip().upper()
        if not code or not _SYMBOL_RE.match(code) or code in seen:
            continue
        seen.add(code)
        title = row.get("title")
        sectors = row.get("sectors")
        sector: str | None = None
        if isinstance(sectors, list) and sectors:
            sector = ",".join(str(s) for s in sectors)
        out.append(
            DiscoveredSymbol(
                symbol=code,
                name=str(title).strip() if title else None,
                sector=sector,
            )
        )
    return out


async def fetch_discovered_symbols(
    *,
    url: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[DiscoveredSymbol]:
    """Pull the candidate BIST universe from the discover endpoint."""
    endpoint = (url or os.environ.get("BIST_DISCOVER_URL") or DEFAULT_DISCOVER_URL).strip()
    headers = {"User-Agent": os.environ.get("BIST_DISCOVER_USER_AGENT", "Matrix-Ingestion/1.0")}
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=True)
    try:
        resp = await client.get(endpoint, headers=headers)
        resp.raise_for_status()
        payload = resp.json()
    finally:
        if owns_client:
            await client.aclose()

    symbols = _parse_fintables_payload(payload)
    if not symbols:
        raise RuntimeError(f"BIST discover returned 0 symbols from {endpoint}")
    logger.info(f"BIST discover: fetched {len(symbols)} symbols from {endpoint}")
    return symbols


async def refresh_universe(
    *,
    url: str | None = None,
    bootstrap_active: bool | None = None,
) -> int:
    """Upsert discovered symbols and deactivate delisted tickers.

    bootstrap_active:
      - None (default): if `bist_symbols` is empty → activate all discovered;
        otherwise new symbols start inactive (universe manager promotes winners).
      - True/False: force initial active flag on insert.
    """
    discovered = await fetch_discovered_symbols(url=url)
    now = datetime.now(UTC)
    discovered_codes = {s.symbol for s in discovered}

    async with session_scope() as session:
        existing_count = (
            await session.execute(select(BistSymbol.symbol))
        ).all()
        table_empty = len(existing_count) == 0

        rows = []
        for s in discovered:
            active = True
            if bootstrap_active is not None:
                active = bootstrap_active
            elif not table_empty:
                # Existing row keeps its active flag on conflict; new rows probe inactive.
                active = False
            rows.append(
                {
                    "symbol": s.symbol,
                    "name": s.name,
                    "sector": s.sector,
                    "index_membership": s.index_membership,
                    "active": active,
                    "last_refreshed_at": now,
                }
            )

        stmt = pg_insert(BistSymbol).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["symbol"],
            set_={
                "name": stmt.excluded.name,
                "sector": stmt.excluded.sector,
                "index_membership": stmt.excluded.index_membership,
                "last_refreshed_at": stmt.excluded.last_refreshed_at,
                # Never overwrite `active` on update — universe manager owns that.
            },
        )
        result = await session.execute(stmt)
        upserted = result.rowcount or len(rows)

        if discovered_codes:
            await session.execute(
                update(BistSymbol)
                .where(BistSymbol.symbol.notin_(discovered_codes))
                .values(active=False, last_refreshed_at=now)
            )
            if bootstrap_active is True:
                await session.execute(
                    update(BistSymbol)
                    .where(BistSymbol.symbol.in_(discovered_codes))
                    .values(active=True, last_refreshed_at=now)
                )

    logger.info(f"BIST discover: upserted {upserted} symbols (table_empty={table_empty})")
    return upserted
