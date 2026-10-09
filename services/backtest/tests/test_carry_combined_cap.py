"""A carry's per-trade cap binds the COMBINED notional of its two legs.

The live gate (`live_gate.should_submit_live`) and `CarryExecutor` cap a carry
on both legs together; the paper engine used to cap each leg, so paper booked
2x the dollars live would at the same `max_position_pct`. Paper now sizes the
leg at half the cap, under every sizing branch (risk multiplier and Kelly),
and directional sizing is unchanged.

Engine tests run on the synthetic TEST_ASSET wallet (tests.isolated_market):
equity $10 000, max_position_pct 2 % -> $200 per trade -> $100 a carry leg.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

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
from matrix_shared.models import MarketTrade, Outcome, PaperPosition, Prediction, Wallet  # noqa: E402
from matrix_shared.models.slot_config import StrategySlotConfig  # noqa: E402

import backtest.paper_trade as PT  # noqa: E402
from backtest import carry_books as CB  # noqa: E402
from tests.isolated_market import TEST_ASSET as ASSET, isolated_wallets  # noqa: E402

SYM = "TEST_CAPUSDT"
CTX = {"funding_rate_8h": "-0.01", "borrow_rate_hourly": "0.00002",
       "spot_venue": "bybit", "spot_symbol": SYM}


def _ladder(mid: float, n: int = 20, usd: float = 10_000) -> CB.Book:
    bids = [(mid * (1 - (1 + 2 * i) / 1e4), usd / mid) for i in range(n)]
    asks = [(mid * (1 + (1 + 2 * i) / 1e4), usd / mid) for i in range(n)]
    return CB.Book(bids, asks, "test")


DEEP = _ladder(100.0)


def _wallet(cash: str, locked: str = "0", pct: str = "0.02") -> Wallet:
    return Wallet(cash_usd=Decimal(cash), locked_usd=Decimal(locked), max_position_pct=Decimal(pct))


# ── pure ─────────────────────────────────────────────────────────────────────

def test_leg_cap_is_half_the_per_trade_cap():
    w = _wallet("9800", "200")
    assert PT.carry_leg_cap(Decimal("10000"), w) == Decimal("100.00")
    # the worked example from the finding: equity 9 841 at 2 % -> $196.82 per
    # trade -> $98.41 a leg (it used to be $196.82 a leg)
    w = _wallet("9272.49", "568.54")
    assert PT.carry_leg_cap(Decimal("9841.03"), w) == Decimal("98.41")


def test_leg_cap_uses_the_lower_of_marked_and_book_equity():
    w = _wallet("9800", "200")
    # an unrealised gain does not lift the leg above what the gate allows
    assert PT.carry_leg_cap(Decimal("10500"), w) == Decimal("100.00")
    # an unrealised loss lowers it, as it lowers every other size
    assert PT.carry_leg_cap(Decimal("9000"), w) == Decimal("90.00")
    # rounds down: two legs never exceed the cap by a cent
    w = _wallet("10000.01")
    assert 2 * PT.carry_leg_cap(Decimal("10000.01"), w) <= Decimal("10000.01") * Decimal("0.02")


# ── engine ───────────────────────────────────────────────────────────────────

_trade_ids: list[str] = []


@pytest_asyncio.fixture
async def cap_wallet(monkeypatch):
    sid = f"TEST_cap_{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("MATRIX_CARRY_MIRROR", "false")  # checked against the executor below

    async def legs(symbol, context, **_kw):
        return DEEP, DEEP

    monkeypatch.setattr(CB, "legs", legs)

    async def db_book(venue, category, symbol, at=None):  # the executor's pre-trade check
        return DEEP

    async def no_quote(venue, coin, at=None):
        return None
    monkeypatch.setattr(CE, "db_book", db_book)
    monkeypatch.setattr(CE, "db_borrow_quote", no_quote)
    async with isolated_wallets() as (wallet_id, _shadow):
        async with shared_session_scope() as session:
            session.add(StrategySlotConfig(
                strategy_id=sid, asset_class=ASSET, wallet_id=wallet_id,
                allocated_slots=4, perf_score=0.5, consecutive_losses=0,
            ))
        tid = f"carry-cap-{uuid.uuid4().hex[:8]}"
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


def _kelly_at_gate(monkeypatch):
    """A measured, confirmed edge whose Kelly fraction exceeds the gate: the
    size is the gate's, so any carry/directional difference is the rule."""
    async def kelly(strategy_ids, *, asset_class, concurrency):
        return {sid: {"kelly_f": 0.5, "edge_frac": 0.05} for sid in strategy_ids}

    async def confirmed(strategy_id, asset_class):
        return True

    monkeypatch.setattr(PT, "_kelly_fractions", kelly)
    monkeypatch.setattr(PT, "_promotion_confirmed", confirmed)


async def _pred(sid: str, side: str, ctx: dict) -> uuid.UUID:
    pid = uuid.uuid4()
    now = datetime.now(timezone.utc)
    async with shared_session_scope() as session:
        session.add(Prediction(
            id=pid, strategy_id=sid, strategy_version=1, asset_class=ASSET, symbol=SYM,
            exchange="bybit", side=side, confidence=Decimal("0.9"),
            horizon_seconds=172800, generated_at=now, entry_price_ref=Decimal("100"),
            close_by=now + timedelta(seconds=172800), status="open", context=dict(ctx),
        ))
    return pid


async def _position(pid: uuid.UUID) -> PaperPosition:
    async with shared_session_scope() as session:
        return (await session.execute(
            select(PaperPosition).where(PaperPosition.prediction_id == pid))).scalar_one()


@pytest.mark.asyncio
async def test_kelly_carry_combined_within_cap_directional_unchanged(cap_wallet, monkeypatch):
    wallet_id, sid = cap_wallet
    _kelly_at_gate(monkeypatch)
    carry = await _pred(sid, "inverse_carry", CTX)
    long = await _pred(sid, "long", {})
    await PT._open_for_market(ASSET)

    per_trade = Decimal("10000") * Decimal("0.02")
    c, d = await _position(carry), await _position(long)
    assert 2 * c.notional_usd <= per_trade
    assert c.notional_usd == Decimal("100.00")  # cap binds: Kelly and the books both allow more
    assert d.notional_usd == per_trade  # directional: one leg, the whole per-trade cap
    async with shared_session_scope() as session:
        pred = await session.get(Prediction, carry)
    assert pred.context["book_open"]["notional_usd"] == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_unmeasured_carry_combined_within_cap(cap_wallet):
    """Risk-multiplier branch (no Kelly, not confirmed): still half the cap."""
    wallet_id, sid = cap_wallet
    pid = await _pred(sid, "inverse_carry", CTX)
    await PT._open_for_market(ASSET)
    pos = await _position(pid)
    assert CB.MIN_LEG_USD <= float(pos.notional_usd) and 2 * pos.notional_usd <= Decimal("200")


@pytest.mark.asyncio
async def test_paper_leg_equals_executor_leg(cap_wallet, monkeypatch):
    wallet_id, sid = cap_wallet
    _kelly_at_gate(monkeypatch)
    pid = await _pred(sid, "inverse_carry", CTX)
    await PT._open_for_market(ASSET)
    pos = await _position(pid)

    # the executor's ceiling for the same wallet is the paper leg, to the cent
    exec_cap = await CE.CarryExecutor._wallet_leg_cap(wallet_id)
    assert exec_cap.quantize(Decimal("0.01")) == pos.notional_usd

    # and a dry-run open of the paper leg is not cut: any gap is price, not size
    spec = CE.InstrumentSpec(qty_step=Decimal("0.001"), min_qty=Decimal("0.001"),
                             tick=Decimal("0.01"), min_notional=Decimal("5"))

    async def books(venue, category, symbol):
        return CE.Book(bids=DEEP.bids, asks=DEEP.asks, source="test")

    async def specs(venue, category, symbol):
        return spec

    async def quote(venue, coin):
        return CE.BorrowQuote(hourly=Decimal("0.00002"), max_borrow=Decimal("1000000"), borrowable=True)

    ex = CE.CarryExecutor(mode="dry_run", books=books, specs=specs, borrow_quote=quote, store=CE.MemoryStore())
    it = CE.CarryIntent(prediction_id=uuid.uuid4(), strategy_id=sid, strategy_version=1, wallet_id=wallet_id,
                        perp_symbol=SYM, spot_venue="bybit", spot_symbol=SYM, leg_usd=pos.notional_usd,
                        asset_class=ASSET, confirmed=True, borrow_hourly=Decimal("0.00002"))
    res = await ex.open(it)
    assert res.status == "done", res.reasons
    assert abs(Decimal(str(res.leg_usd)) - pos.notional_usd) <= spec.qty_step * Decimal("100")
    assert res.combined_usd <= 200.0
