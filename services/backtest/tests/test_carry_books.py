"""Book-priced spot-hedged carries (backtest.carry_books): sized and charged on
both legs' books, borrow per started hour from the recorded rate series (entry
quote x stress where it has gaps), and no position at all without a spot book.

Engine tests run on the synthetic TEST_ASSET wallet (tests.isolated_market).
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

from matrix_shared import local_session_scope, shared_session_scope  # noqa: E402
from matrix_shared.models import MarketTrade, Outcome, PaperPosition, Prediction  # noqa: E402
from matrix_shared.models.slot_config import StrategySlotConfig  # noqa: E402

from backtest import carry_books as CB  # noqa: E402
from tests.isolated_market import TEST_ASSET as ASSET, isolated_wallets  # noqa: E402

SYM = "TEST_NFCBOOKUSDT"
CTX = {"funding_rate_8h": "-0.01", "borrow_rate_hourly": "0.00002",
       "spot_venue": "bybit", "spot_symbol": "TEST_NFCBOOKUSDT"}


def ladder(mid: float, half_spread_bps: float, usd_per_level: float, n: int = 20,
           step_bps: float = 2.0) -> CB.Book:
    bids, asks = [], []
    for i in range(n):
        off = (half_spread_bps + i * step_bps) / 1e4
        bp, ap = mid * (1 - off), mid * (1 + off)
        bids.append((bp, usd_per_level / bp))
        asks.append((ap, usd_per_level / ap))
    return CB.Book(bids, asks, "test")


DEEP = ladder(100.0, 1.0, 10_000)


# ── pure ─────────────────────────────────────────────────────────────────────

def test_open_sizing_caps_at_impact_and_unconfirmed_ceiling():
    usd, patch = CB.size_and_price_open(DEEP, DEEP, 5_000, confirmed=False)
    assert usd == CB.UNCONFIRMED_MAX_LEG_USD  # $500 until `confirmed`
    assert patch["perp_buy_bps"] == pytest.approx(1.0) and patch["spot_sell_bps"] == pytest.approx(1.0)
    usd, _ = CB.size_and_price_open(DEEP, DEEP, 5_000, confirmed=True)
    assert usd == 5_000  # confirmed lifts the ceiling, never the engine's own size
    usd, _ = CB.size_and_price_open(DEEP, DEEP, 120, confirmed=True)
    assert usd == 120

    thin_spot = ladder(100.0, 4.0, 300)  # levels at 4, 6, 8, ... bps of $300
    usd, patch = CB.size_and_price_open(DEEP, thin_spot, 5_000, confirmed=True)
    assert usd == pytest.approx(CB.max_usd_within(thin_spot.bids, thin_spot.mid, CB.MAX_LEG_IMPACT_BPS))
    assert CB.walk_bps(thin_spot.bids, thin_spot.mid, usd) == pytest.approx(CB.MAX_LEG_IMPACT_BPS, rel=1e-3)
    assert patch["leg_cap_usd"] == pytest.approx(usd, abs=0.01)

    wide = ladder(100.0, 15.0, 10_000)  # half-spread alone above the cap
    usd, reason = CB.size_and_price_open(DEEP, wide, 5_000, confirmed=True)
    assert usd is None and "leg size" in reason


def test_close_cost_uses_books_then_entry_estimate():
    _, book_open = CB.size_and_price_open(DEEP, ladder(100.0, 5.0, 10_000), 500, confirmed=False)
    now_books = CB.close_cost_bps(book_open, DEEP, ladder(100.0, 20.0, 10_000), 500)
    assert now_books["close_source"] == "book"
    assert now_books["spot_buy_bps"] == pytest.approx(20.0)  # the book at close, not at open
    assert now_books["total_bps"] == pytest.approx(31.0 + 1 + 5 + 1 + 20, rel=1e-4)
    fallback = CB.close_cost_bps(book_open, DEEP, None, 500)
    assert fallback["close_source"] == "entry_estimate"
    assert fallback["spot_buy_bps"] == pytest.approx(5.0)


def test_borrow_per_started_hour_at_stress():
    n, h = Decimal("500"), Decimal("0.00002")
    assert CB.borrow_charge(n, h, 3.0, 0) == 0
    assert CB.borrow_charge(n, h, 3.0, 60) == n * h * 3  # first hour is charged in full
    assert CB.borrow_charge(n, h, 3.0, 3600) == n * h * 3
    assert CB.borrow_charge(n, h, 3.0, 48 * 3600 + 1) == n * h * 3 * 49


def test_series_rates_step_function_and_gaps():
    t0 = datetime(2026, 10, 9, 12, 4, tzinfo=timezone.utc)
    r = lambda m, v: (t0 + timedelta(minutes=m), Decimal(v))  # noqa: E731
    rows = [r(-4, "1"), r(50, "2"), r(170, "3")]  # hours start at 0, 60, 120, 180, 240 min
    got = CB.series_rates(rows, t0, t0 + timedelta(minutes=241))
    # h0: in force at start; h1: row 50 (10 min old); h2: row 50 is 70 min old (<= 75)
    # h3: row 170 in force; h4: row 170 is 70 min old
    assert got == [Decimal("1"), Decimal("2"), Decimal("2"), Decimal("3"), Decimal("3")]
    got = CB.series_rates([r(30, "5")], t0, t0 + timedelta(minutes=200))
    # h0: nothing at the start, first row inside the hour; h1 row 30 is 30 min old;
    # h2 (start 120): 90 min old and nothing inside -> gap; h3 (180..200): gap
    assert got == [Decimal("5"), Decimal("5"), None, None]
    assert CB.series_rates([], t0, t0 + timedelta(minutes=1)) == [None]


def test_borrow_from_series_charges_series_x1_and_stressed_gaps():
    n, entry = Decimal("100"), Decimal("0.0001")
    charge, info = CB.borrow_from_series(n, [Decimal("0.0002"), Decimal("0.0004")], entry, 3.0)
    assert charge == n * Decimal("0.0006") and info["borrow_source"] == "series"
    charge, info = CB.borrow_from_series(n, [Decimal("0.0002"), None], entry, 3.0)
    assert charge == n * (Decimal("0.0002") + entry * 3) and info["borrow_source"] == "mixed"
    assert (info["borrow_hours_series"], info["borrow_hours_fallback"]) == (1, 1)
    charge, info = CB.borrow_from_series(n, [None, None], entry, 3.0)
    assert charge == n * entry * 3 * 2 and info["borrow_source"] == "stressed_entry"


def test_book_priced_carry_holds_to_horizon():
    """No funding-flip exit for a carry that names its spot leg; flat carries keep it."""
    from backtest.paper_trade import _flip_exit_applies
    assert not _flip_exit_applies(Prediction(context=dict(CTX)))
    assert _flip_exit_applies(Prediction(context={"funding_rate_8h": "-0.001"}))
    assert _flip_exit_applies(Prediction(context=None))


# ── engine ───────────────────────────────────────────────────────────────────

_trade_ids: list[str] = []


@pytest_asyncio.fixture
async def book_wallet():
    sid = f"TEST_nfc_{uuid.uuid4().hex[:8]}"
    async with isolated_wallets() as (wallet_id, _shadow):
        async with shared_session_scope() as session:
            session.add(StrategySlotConfig(
                strategy_id=sid, asset_class=ASSET, wallet_id=wallet_id,
                allocated_slots=4, perf_score=0.5, consecutive_losses=0,
            ))
        tid = f"nfc-books-{uuid.uuid4().hex[:8]}"
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


async def _pred(sid: str, *, ctx: dict = CTX, hours_ago: float = 0.0) -> uuid.UUID:
    pid = uuid.uuid4()
    gen = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    async with shared_session_scope() as session:
        session.add(Prediction(
            id=pid, strategy_id=sid, strategy_version=1, asset_class=ASSET, symbol=SYM,
            exchange="bybit", side="inverse_carry", confidence=Decimal("0.9"),
            horizon_seconds=172800, generated_at=gen, entry_price_ref=Decimal("100"),
            close_by=gen + timedelta(seconds=172800), status="open", context=dict(ctx),
        ))
    return pid


def _legs(monkeypatch, perp, spot):
    async def fake(symbol, context):
        return perp, spot
    monkeypatch.setattr(CB, "legs", fake)


@pytest.mark.asyncio
async def test_no_spot_book_no_position(book_wallet, monkeypatch):
    wallet_id, sid = book_wallet
    _legs(monkeypatch, DEEP, None)
    pid = await _pred(sid)
    from backtest.paper_trade import _open_for_market
    await _open_for_market(ASSET)
    async with shared_session_scope() as session:
        pos = (await session.execute(
            select(PaperPosition).where(PaperPosition.prediction_id == pid))).scalar_one_or_none()
    assert pos is None


@pytest.mark.asyncio
async def test_open_stamps_book_and_sizes_to_impact(book_wallet, monkeypatch):
    wallet_id, sid = book_wallet
    thin = ladder(100.0, 4.0, 15)  # 7 levels (~$105) average 10 bps
    _legs(monkeypatch, DEEP, thin)
    pid = await _pred(sid)
    from backtest.paper_trade import _open_for_market
    await _open_for_market(ASSET)
    async with shared_session_scope() as session:
        pos = (await session.execute(
            select(PaperPosition).where(PaperPosition.prediction_id == pid))).scalar_one()
        pred = await session.get(Prediction, pid)
    cap = CB.leg_cap_usd(DEEP, thin)
    assert CB.MIN_LEG_USD <= float(pos.notional_usd) <= cap + 0.01
    assert float(pos.notional_usd) <= 200  # wallet risk gate: 2 % of $10k
    bo = pred.context["book_open"]
    assert bo["notional_usd"] == pytest.approx(float(pos.notional_usd), abs=0.01)
    assert bo["spot_sell_bps"] <= CB.MAX_LEG_IMPACT_BPS + 1e-6


@pytest.mark.asyncio
async def test_close_charges_books_fees_and_stressed_borrow(book_wallet, monkeypatch):
    """No settlements crossed (no ticker rows for SYM): PnL is exactly minus
    the book cost minus 10 started hours of borrow at 3x."""
    wallet_id, sid = book_wallet
    _legs(monkeypatch, DEEP, ladder(100.0, 5.0, 10_000))
    book_open = {"perp_buy_bps": 1.0, "spot_sell_bps": 5.0, "perp_sell_est_bps": 1.0,
                 "spot_buy_est_bps": 5.0, "borrow_stress": 3.0, "notional_usd": 200.0}
    pid = await _pred(sid, ctx={**CTX, "book_open": book_open}, hours_ago=9.5)
    notional = Decimal("200")
    async with shared_session_scope() as session:
        session.add(PaperPosition(
            id=uuid.uuid4(), prediction_id=pid, symbol=SYM, exchange="bybit", asset_class=ASSET,
            side="inverse_carry", notional_usd=notional,
            opened_at=datetime.now(timezone.utc) - timedelta(hours=9.5),
            opened_price=Decimal("100"), status="open", wallet_id=wallet_id,
        ))
    from backtest.paper_trade import _close_position
    async with shared_session_scope() as session:
        pos = (await session.execute(
            select(PaperPosition).where(PaperPosition.prediction_id == pid))).scalar_one()
        pred = await session.get(Prediction, pid)
    assert await _close_position(pos, pred, "hit_horizon", datetime.now(timezone.utc))
    async with shared_session_scope() as session:
        out = (await session.execute(select(Outcome).where(Outcome.prediction_id == pid))).scalar_one()
        pred = await session.get(Prediction, pid)
    cost_bps = Decimal("31") + 1 + 5 + 1 + 5
    borrow = notional * Decimal("0.00002") * 3 * 10
    assert out.pnl_usd == pytest.approx(-(notional * cost_bps / 10000) - borrow, abs=Decimal("0.0001"))
    assert pred.context["book_close"]["close_source"] == "book"
    assert Decimal(pred.context["borrow_charged_usd"]) == borrow
    assert pred.context["borrow_source"] == "stressed_entry"  # no recorded series for the test coin


@pytest.mark.asyncio
async def test_close_charges_recorded_borrow_series(book_wallet, monkeypatch):
    """With a recorded series covering the hold, borrow is the series x1, not
    the entry quote x stress."""
    from sqlalchemy import text
    wallet_id, sid = book_wallet
    coin = f"TESTBR{uuid.uuid4().hex[:6].upper()}"
    _legs(monkeypatch, DEEP, DEEP)
    book_open = {"perp_buy_bps": 1.0, "spot_sell_bps": 1.0, "perp_sell_est_bps": 1.0,
                 "spot_buy_est_bps": 1.0, "borrow_stress": 3.0, "notional_usd": 200.0}
    ctx = {**CTX, "borrow_venue": "binance", "spot_venue": "binance", "spot_symbol": f"{coin}USDT",
           "book_open": book_open}
    now = datetime.now(timezone.utc)
    opened = now - timedelta(hours=2.5)
    async with local_session_scope() as session:
        await session.execute(text(
            "INSERT INTO margin_borrow_rates (venue, coin, ts, hourly_rate) VALUES "
            "('binance', :c, :a, 0.00001), ('binance', :c, :b, 0.00005)"),
            {"c": coin, "a": opened - timedelta(minutes=5), "b": opened + timedelta(minutes=70)})
    try:
        pid = await _pred(sid, ctx=ctx, hours_ago=2.5)
        notional = Decimal("200")
        async with shared_session_scope() as session:
            session.add(PaperPosition(
                id=uuid.uuid4(), prediction_id=pid, symbol=SYM, exchange="bybit", asset_class=ASSET,
                side="inverse_carry", notional_usd=notional, opened_at=opened,
                opened_price=Decimal("100"), status="open", wallet_id=wallet_id,
            ))
        from backtest.paper_trade import _close_position
        async with shared_session_scope() as session:
            pos = (await session.execute(
                select(PaperPosition).where(PaperPosition.prediction_id == pid))).scalar_one()
            pred = await session.get(Prediction, pid)
        assert await _close_position(pos, pred, "hit_horizon", now)
        async with shared_session_scope() as session:
            pred = await session.get(Prediction, pid)
        # hour starts +0, +1h: the -5 min row is in force (5 / 65 min old) -> 1e-5 each;
        # +2h: the +70 min row -> 5e-5. Entry quote x3 would have been 3 x 2e-5 x 3.
        assert Decimal(pred.context["borrow_charged_usd"]) == notional * Decimal("0.00007")
        assert pred.context["borrow_source"] == "series"
        assert pred.context["borrow_hours_series"] == 3
    finally:
        async with local_session_scope() as session:
            await session.execute(text("DELETE FROM margin_borrow_rates WHERE venue = 'binance' AND coin = :c"),
                                  {"c": coin})
