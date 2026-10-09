"""Test fixtures for reflection grant tests.

Same SHARED-postgres approach as services/execution/tests: per-test
UUID-scoped strategy_id with cleanup.
"""

from __future__ import annotations

import inspect
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest_asyncio
from sqlalchemy import delete, select

from matrix_shared import shared_session_scope
from matrix_shared.models import Outcome, PaperTradeCertificate, Prediction

TEST_SHARED_DSN = os.environ.get(
    "REFLECTION_TEST_SHARED_DSN",
    "postgres://matrix:matrix_dev_only@localhost:5433/matrix_shared",
)
os.environ.setdefault("SHARED_DATABASE_URL", TEST_SHARED_DSN)
os.environ.setdefault("LOCAL_DATABASE_URL", TEST_SHARED_DSN)


@pytest_asyncio.fixture(autouse=True)
async def _db_writes_never_commit(request):
    """These tests run against the LIVE databases and call global passes:
    score_strategy_slots() with MIN_N_FOR_SLOT_CHANGE=1 re-scores every live
    slot row, revoke_breached_certificates(0.15) judges every live cert against
    a cap tighter than the operator's, maybe_file_dev_task writes a 'pending'
    row the live dev_agent worker claims. Every async test therefore runs in
    an outer transaction that is rolled back — nothing it writes is committed."""
    if not inspect.iscoroutinefunction(request.function):
        yield
        return
    from matrix_shared.testing import db_writes_rolled_back

    async with db_writes_rolled_back():
        yield


@pytest_asyncio.fixture(autouse=True)
async def _no_background_edge_study(monkeypatch):
    """`score_strategy_slots` asks `edge_study.strategy_edge` for every live
    slot row; a cache miss spawns a background study task. Under the rollback
    fixture that task shared the test's single connection and interleaved its
    savepoints with the scorer's (test_auto_cut_on_consecutive_losses failed
    ~1 in 30), ran full studies against live data, and wrote TEST_* rows into
    the live edge_cache.json. Tests that need an edge answer monkeypatch
    `strategy_edge` / `_entry_edge_verdict` themselves."""
    import matrix_shared.edge_study as E

    async def no_refresh(key, days):
        E._refreshing.discard(key)

    monkeypatch.setattr(E, "_refresh_edge", no_refresh)
    monkeypatch.setattr(E, "_save_disk_cache", lambda: None)
    yield


@pytest_asyncio.fixture
async def cert_cleanup():
    """Collector for (strategy_id, asset_class, version) triples that the
    test creates. After-test sweeps cert rows + any predictions/outcomes
    with matching strategy_id so the next test sees a clean slate."""
    collected: list[tuple[str, str, int]] = []
    yield collected
    if collected:
        sids = sorted({sid for sid, _, _ in collected})
        async with shared_session_scope() as session:
            for sid in sids:
                pred_ids = list(
                    (await session.execute(
                        select(Prediction.id).where(Prediction.strategy_id == sid)
                    )).scalars()
                )
                if pred_ids:
                    await session.execute(
                        delete(Outcome).where(Outcome.prediction_id.in_(pred_ids))
                    )
                    await session.execute(
                        delete(Prediction).where(Prediction.id.in_(pred_ids))
                    )
            for sid, ac, ver in collected:
                await session.execute(
                    delete(PaperTradeCertificate)
                    .where(PaperTradeCertificate.strategy_id == sid)
                    .where(PaperTradeCertificate.asset_class == ac)
                    .where(PaperTradeCertificate.version == ver)
                )


@pytest_asyncio.fixture
async def seed_outcomes():
    """Factory: insert N outcomes for (strategy_id, asset_class, version)."""
    async def _seed(
        strategy_id: str,
        asset_class: str,
        version: int,
        *,
        n: int = 10,
        win_rate: float = 0.6,
        pnl_per_trade_usd: float = 1.0,
        spacing_s: int = 120,
    ) -> None:
        now = datetime.now(timezone.utc)
        # Two-session insert: predictions first (FK target), outcomes after.
        # SQLAlchemy topo-sort can't reliably order both in one session.
        pairs: list[tuple[uuid.UUID, datetime, bool]] = []
        async with shared_session_scope() as session:
            for i in range(n):
                pid = uuid.uuid4()
                # One horizon apart: outcomes are counted per episode, and
                # signals inside each other's horizon would be one bet.
                gen_ts = now - timedelta(seconds=spacing_s * (n - i))
                is_win = (i / max(n, 1)) < win_rate
                pairs.append((pid, gen_ts, is_win))
                session.add(
                    Prediction(
                        id=pid,
                        strategy_id=strategy_id,
                        strategy_version=version,
                        asset_class=asset_class,
                        symbol="BTCUSDT",
                        exchange="bybit",
                        side="long",
                        confidence=Decimal("0.5"),
                        horizon_seconds=60,
                        generated_at=gen_ts,
                        close_by=gen_ts + timedelta(seconds=60),
                        entry_price_ref=Decimal("100"),
                        status="closed",
                    )
                )
        async with shared_session_scope() as session:
            for pid, gen_ts, is_win in pairs:
                pnl = Decimal(str(pnl_per_trade_usd if is_win else -pnl_per_trade_usd))
                session.add(
                    Outcome(
                        id=uuid.uuid4(),
                        prediction_id=pid,
                        asset_class=asset_class,
                        observed_at=gen_ts + timedelta(seconds=120),
                        pnl_usd=pnl,
                        pnl_pct=Decimal("0.01") if is_win else Decimal("-0.01"),
                        score=Decimal("1.0") if is_win else Decimal("-1.0"),
                        reason="hit_horizon",
                    )
                )

    return _seed
