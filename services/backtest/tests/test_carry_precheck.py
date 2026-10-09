"""Paper skips a book-priced carry the live executor would abort.

The executor's pre-trade checks (carry_executor.precheck_open: both books, the
borrow quote <= 1.5x the signal's, the borrow quota, margin) were not applied
in paper, so the shadow book could count episodes live could never have
entered (KAIA, 2026-10-09). Paper now calls the same function: a failing
signal does not open and carries `context.exec_precheck = skipped`; an open
stamps the passing record; an open the dry-run mirror then aborts is tagged
`would_abort` and kept.

Engine tests run on the synthetic TEST_ASSET wallet (tests.isolated_market).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

TEST_LOCAL_DSN = os.environ.get(
    "BACKTEST_TEST_LOCAL_DSN", "postgres://matrix:matrix_dev_only@localhost:5432/matrix"
)
TEST_SHARED_DSN = os.environ.get(
    "BACKTEST_TEST_SHARED_DSN", "postgres://matrix:matrix_dev_only@localhost:5433/matrix_shared"
)
os.environ.setdefault("LOCAL_DATABASE_URL", TEST_LOCAL_DSN)
os.environ.setdefault("SHARED_DATABASE_URL", TEST_SHARED_DSN)

import matrix_shared.carry_executor as CE  # noqa: E402
from matrix_shared import local_session_scope, shared_session_scope  # noqa: E402
from matrix_shared.models import MarketTrade, Outcome, PaperPosition, Prediction  # noqa: E402
from matrix_shared.models.slot_config import StrategySlotConfig  # noqa: E402

import backtest.paper_trade as PT  # noqa: E402
from backtest import carry_books as CB  # noqa: E402
from tests.isolated_market import TEST_ASSET as ASSET, isolated_wallets  # noqa: E402

SYM = "TEST_PRECHKUSDT"
PRICED = Decimal("0.00002")
CTX = {"funding_rate_8h": "-0.01", "borrow_rate_hourly": str(PRICED),
       "spot_venue": "bybit", "spot_symbol": SYM}


def _ladder(mid: float, n: int = 20, usd: float = 10_000) -> CB.Book:
    bids = [(mid * (1 - (1 + 2 * i) / 1e4), usd / mid) for i in range(n)]
    asks = [(mid * (1 + (1 + 2 * i) / 1e4), usd / mid) for i in range(n)]
    return CB.Book(bids, asks, "test")


DEEP = _ladder(100.0)
_trade_ids: list[str] = []


def _market(monkeypatch, *, quote_hourly: Decimal | None, spot_in_db: bool = True) -> None:
    """Paper's own books (CB.legs) are deep; the executor's view (CE.db_book,
    CE.db_borrow_quote) is what the test varies."""
    async def legs(symbol, context, **_kw):
        return DEEP, DEEP

    async def db_book(venue, category, symbol, at=None):
        return DEEP if (category == "linear" or spot_in_db) else None

    async def quote(venue, coin, at=None):
        if quote_hourly is None:
            return None
        return CE.BorrowQuote(hourly=quote_hourly, max_borrow=Decimal("1000000"), borrowable=True)

    monkeypatch.setattr(CB, "legs", legs)
    monkeypatch.setattr(CE, "db_book", db_book)
    monkeypatch.setattr(CE, "db_borrow_quote", quote)
    monkeypatch.setenv("MATRIX_CARRY_MIRROR", "false")


@pytest_asyncio.fixture
async def pc_wallet():
    sid = f"TEST_pc_{uuid.uuid4().hex[:8]}"
    async with isolated_wallets() as (wallet_id, _shadow):
        async with shared_session_scope() as session:
            session.add(StrategySlotConfig(
                strategy_id=sid, asset_class=ASSET, wallet_id=wallet_id,
                allocated_slots=4, perf_score=0.5, consecutive_losses=0,
            ))
        tid = f"carry-pc-{uuid.uuid4().hex[:8]}"
        async with local_session_scope() as session:
            session.add(MarketTrade(
                id=uuid.uuid4(), exchange="bybit", exchange_trade_id=tid, symbol=SYM,
                trade_ts=datetime.now(timezone.utc), side="buy",
                price=Decimal("100"), size=Decimal("1"),
            ))
        _trade_ids.append(tid)
        try:
            yield wallet_id, sid
        finally:
            async with shared_session_scope() as session:
                ids = list((await session.execute(
                    select(Prediction.id).where(Prediction.strategy_id == sid))).scalars())
                await session.execute(delete(Outcome).where(Outcome.prediction_id.in_(ids)))
                await session.execute(delete(PaperPosition).where(PaperPosition.prediction_id.in_(ids)))
                await session.execute(delete(Prediction).where(Prediction.strategy_id == sid))
                await session.execute(delete(StrategySlotConfig).where(StrategySlotConfig.strategy_id == sid))
            async with local_session_scope() as session:
                await session.execute(delete(MarketTrade).where(MarketTrade.exchange == "bybit").where(
                    MarketTrade.exchange_trade_id.in_(_trade_ids)))


async def _pred(sid: str) -> uuid.UUID:
    pid = uuid.uuid4()
    now = datetime.now(timezone.utc)
    async with shared_session_scope() as session:
        session.add(Prediction(
            id=pid, strategy_id=sid, strategy_version=1, asset_class=ASSET, symbol=SYM,
            exchange="bybit", side="inverse_carry", confidence=Decimal("0.9"),
            horizon_seconds=172800, generated_at=now, entry_price_ref=Decimal("100"),
            close_by=now + timedelta(seconds=172800), status="open", context=dict(CTX),
        ))
    return pid


async def _state(pid: uuid.UUID) -> tuple[PaperPosition | None, dict]:
    async with shared_session_scope() as session:
        pos = (await session.execute(
            select(PaperPosition).where(PaperPosition.prediction_id == pid))).scalar_one_or_none()
        pred = await session.get(Prediction, pid)
        return pos, dict(pred.context or {})


@pytest.mark.asyncio
async def test_moved_borrow_quote_skips_and_records_reason(pc_wallet, monkeypatch):
    wallet_id, sid = pc_wallet
    _market(monkeypatch, quote_hourly=PRICED * 2)  # 2x the signal's quote > 1.5x
    pid = await _pred(sid)
    await PT._open_for_market(ASSET)
    pos, ctx = await _state(pid)
    assert pos is None
    ep = ctx["exec_precheck"]
    assert ep["status"] == CE.PRECHECK_SKIPPED and ep["failed"] == "borrow_drift"
    assert ep["reason"].startswith("borrow quote moved")
    assert ep["checks"] == {"books": "pass", "borrow_drift": "fail", "borrow_quota": "skipped", "margin": "skipped"}
    # unchanged decision on the next tick: no second write
    await PT._open_for_market(ASSET)
    pos, ctx2 = await _state(pid)
    assert pos is None and ctx2["exec_precheck"]["ts"] == ep["ts"]


@pytest.mark.asyncio
async def test_no_executor_book_skips_even_when_paper_has_one(pc_wallet, monkeypatch):
    """Paper may price from REST; the executor reads the DB (<= 60 s). Live
    would abort, so paper does not open."""
    wallet_id, sid = pc_wallet
    _market(monkeypatch, quote_hourly=PRICED, spot_in_db=False)
    pid = await _pred(sid)
    await PT._open_for_market(ASSET)
    pos, ctx = await _state(pid)
    assert pos is None
    assert ctx["exec_precheck"]["failed"] == "books" and ctx["exec_precheck"]["reason"] == "no spot book"


@pytest.mark.asyncio
async def test_passing_carry_opens_and_stamps_the_check(pc_wallet, monkeypatch):
    wallet_id, sid = pc_wallet
    _market(monkeypatch, quote_hourly=PRICED * Decimal("1.5"))  # at the limit: still passes
    pid = await _pred(sid)
    await PT._open_for_market(ASSET)
    pos, ctx = await _state(pid)
    assert pos is not None
    ep = ctx["exec_precheck"]
    assert ep["status"] == "pass" and ep["reason"] is None
    assert ep["checks"]["borrow_quota"] == "pass" and ep["checks"]["margin"] == "unknown"
    assert "book_open" in ctx


@pytest.mark.asyncio
async def test_a_skipped_signal_opens_once_the_quote_comes_back(pc_wallet, monkeypatch):
    wallet_id, sid = pc_wallet
    _market(monkeypatch, quote_hourly=PRICED * 3)
    pid = await _pred(sid)
    await PT._open_for_market(ASSET)
    assert (await _state(pid))[0] is None
    _market(monkeypatch, quote_hourly=PRICED)
    await PT._open_for_market(ASSET)
    pos, ctx = await _state(pid)
    assert pos is not None and ctx["exec_precheck"]["status"] == "pass"


@pytest.mark.asyncio
async def test_mirror_abort_tags_would_abort_and_keeps_the_position(pc_wallet, monkeypatch):
    wallet_id, sid = pc_wallet
    _market(monkeypatch, quote_hourly=PRICED)

    async def mirror(**_kw):
        return SimpleNamespace(precheck={"status": CE.PRECHECK_WOULD_ABORT, "failed": "borrow_drift",
                                         "reason": "borrow quote moved"})

    monkeypatch.setattr(PT, "mirror_paper_open", mirror)
    pid = await _pred(sid)
    await PT._open_for_market(ASSET)
    pos, ctx = await _state(pid)
    assert pos is not None and pos.status == "open"
    assert ctx["exec_precheck"]["status"] == CE.PRECHECK_WOULD_ABORT
    assert ctx["exec_precheck"]["source"] == "mirror"
    assert "book_open" in ctx  # the jsonb merge kept the rest of the context
