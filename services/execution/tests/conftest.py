"""Shared test fixtures for execution gate tests.

Talks to the running matrix-postgres-shared container instead of a
throwaway test DB — production schema is needed (wallets, paper_positions,
paper_trade_certificate) and `make migrate-local-shared` already created
them there. Each test gets its own UUID-scoped fixtures and cleans up
after, so the dev-environment data is never touched.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from matrix_shared import shared_session_scope
from matrix_shared.models import PaperPosition, PaperTradeCertificate, Wallet

# Point at the SHARED postgres exposed on localhost. Different host port
# (5433) — matrix-postgres-shared is on the second compose stack.
TEST_SHARED_DSN = os.environ.get(
    "EXECUTION_TEST_SHARED_DSN",
    "postgres://matrix:matrix_dev_only@localhost:5433/matrix_shared",
)
# matrix_shared.db reads SHARED_DATABASE_URL; we set it for the test process
# only — doesn't leak to other shells.
os.environ.setdefault("SHARED_DATABASE_URL", TEST_SHARED_DSN)
os.environ.setdefault("LOCAL_DATABASE_URL", TEST_SHARED_DSN)


@pytest_asyncio.fixture
async def wallet_id() -> uuid.UUID:
    """Fresh wallet per test with sane defaults; cleaned up after."""
    wid = uuid.uuid4()
    async with shared_session_scope() as session:
        session.add(
            Wallet(
                id=wid,
                name=f"test-{wid}",
                starting_capital_usd=Decimal("10000"),
                cash_usd=Decimal("10000"),
                locked_usd=Decimal("0"),
                max_position_pct=Decimal("0.02"),
                max_concurrent_positions=5,
                daily_loss_circuit_pct=Decimal("0.05"),
                day_start_equity=Decimal("10000"),
                day_start_at=datetime.now(timezone.utc),
            )
        )
    yield wid
    async with shared_session_scope() as session:
        # Cascade isn't set on paper_positions FK by name; clean manually
        # to be safe even when individual tests forgot to delete positions.
        await session.execute(delete(PaperPosition).where(PaperPosition.wallet_id == wid))
        await session.execute(delete(Wallet).where(Wallet.id == wid))


@pytest_asyncio.fixture
async def grant_cert():
    """Factory: grant a fresh certificate (with optional validity window),
    returning the strategy descriptor. Cleaned up after the test."""
    created: list[tuple[str, str, int]] = []

    async def _grant(
        strategy_id: str | None = None,
        asset_class: str = "crypto",
        version: int = 1,
        *,
        validity_hours: int | None = 24,
        status: str = "granted",
    ) -> tuple[str, str, int]:
        sid = strategy_id or f"test_strat_{uuid.uuid4().hex[:8]}"
        until = (
            datetime.now(timezone.utc) + timedelta(hours=validity_hours)
            if validity_hours is not None
            else None
        )
        async with shared_session_scope() as session:
            session.add(
                PaperTradeCertificate(
                    strategy_id=sid,
                    asset_class=asset_class,
                    version=version,
                    status=status,
                    n_outcomes=300,
                    observation_days=70,
                    win_rate=Decimal("0.42"),
                    avg_pnl_usd=Decimal("0.50"),
                    total_pnl_usd=Decimal("150"),
                    max_drawdown_pct=Decimal("0.08"),
                    granted_at=datetime.now(timezone.utc),
                    granted_by="test",
                    validity_until=until,
                )
            )
        created.append((sid, asset_class, version))
        return sid, asset_class, version

    yield _grant

    async with shared_session_scope() as session:
        for sid, ac, v in created:
            await session.execute(
                delete(PaperTradeCertificate)
                .where(PaperTradeCertificate.strategy_id == sid)
                .where(PaperTradeCertificate.asset_class == ac)
                .where(PaperTradeCertificate.version == v)
            )
