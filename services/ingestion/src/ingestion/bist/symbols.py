"""Seed / refresh the `bist_symbols` table.

Run once at bootstrap, and again on a cadence (daily) to pick up changes.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy.dialects.postgresql import insert as pg_insert

from matrix_shared import session_scope
from matrix_shared.models import BistSymbol

from ingestion.bist.universe import deduped_seed


async def seed_universe() -> int:
    """Upsert the embedded seed list into `bist_symbols`. Returns inserted count."""
    seed = deduped_seed()
    rows = [
        {
            "symbol": s.symbol,
            "name": s.name,
            "sector": s.sector,
            "index_membership": s.index_membership,
            "active": True,
            "last_refreshed_at": datetime.now(UTC),
        }
        for s in seed
    ]

    async with session_scope() as session:
        stmt = pg_insert(BistSymbol).values(rows)
        update_cols = {
            "name": stmt.excluded.name,
            "sector": stmt.excluded.sector,
            "index_membership": stmt.excluded.index_membership,
            "active": stmt.excluded.active,
            "last_refreshed_at": stmt.excluded.last_refreshed_at,
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=["symbol"], set_=update_cols
        )
        result = await session.execute(stmt)
        return result.rowcount or 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed BIST symbol universe")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    args = parser.parse_args()
    _ = args  # currently only "once" mode; future: --refresh-from-kap

    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level: <5} | {message}")

    count = asyncio.run(seed_universe())
    logger.info(f"bist_symbols upserted: {count}")


if __name__ == "__main__":
    main()
