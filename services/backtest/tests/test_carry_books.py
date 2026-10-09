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

import matrix_shared.carry_executor as CE  # noqa: E402
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


def test_thin_book_close_is_penalised_never_the_calm_estimate():
    """A book that is there but cannot fill the leg is the squeeze case: the
    open-time estimate (5 bps) is the calm-day price. The visible depth is
    walked and the rest charged at the worst level + the penalty, and the
    leg is never below the estimate."""
    book_open = {"perp_buy_bps": 1.0, "spot_sell_bps": 5.0, "perp_sell_est_bps": 1.0, "spot_buy_est_bps": 5.0}
    thin = ladder(100.0, 3.0, 50, n=4)  # $200 visible at 3/5/7/9 bps, the leg is $500
    assert CB.walk_bps(thin.asks, thin.mid, 500) is None
    c = CB.close_cost_bps(book_open, DEEP, thin, 500)
    assert c["close_source"] == "thin_book"
    expect = (200 * 6.0 + 300 * (9.0 + CB.THIN_BOOK_PENALTY_BPS)) / 500
    assert c["spot_buy_bps"] == pytest.approx(expect, rel=1e-3)
    assert c["spot_buy_bps"] > book_open["spot_buy_est_bps"]
    assert c["total_bps"] == pytest.approx(31.0 + 1 + 5 + 1 + expect, rel=1e-4)
    # the penalised walk never undercuts a dearer estimate
    dear = {**book_open, "spot_buy_est_bps": 120.0}
    assert CB.close_cost_bps(dear, DEEP, thin, 500)["spot_buy_bps"] == pytest.approx(120.0)
    # thin perp leg too; the mark is never cheaper than the close
    for perp, spot in [(thin, thin), (DEEP, thin), (thin, None)]:
        close = CB.close_cost_bps(book_open, perp, spot, 500)
        assert close["close_source"] == "thin_book"
        assert CB.mark_cost_bps(book_open, perp, spot, 500) >= close["total_bps"] - 1e-9
    assert CB.thin_walk_bps(thin.asks, thin.mid, 150) == pytest.approx(CB.walk_bps(thin.asks, thin.mid, 150))


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
    async def fake(symbol, context, **_kw):
        return perp, spot
    monkeypatch.setattr(CB, "legs", fake)

    # The executor's pre-trade check (carry_executor.paper_open_precheck)
    # reads the same books; no recorded borrow quote = unknown, not blocking.
    async def db_book(venue, category, symbol, at=None):
        return perp if category == "linear" else spot

    async def no_quote(venue, coin, at=None):
        return None
    monkeypatch.setattr(CE, "db_book", db_book)
    monkeypatch.setattr(CE, "db_borrow_quote", no_quote)
    monkeypatch.setenv("MATRIX_CARRY_MIRROR", "false")


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
    assert 2 * float(pos.notional_usd) <= 200  # both legs within the risk gate: 2 % of $10k
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
async def test_thin_book_close_records_thin_book_and_charges_the_penalty(book_wallet, monkeypatch):
    wallet_id, sid = book_wallet
    thin = ladder(100.0, 3.0, 50, n=4)  # $200 visible, the leg is $500
    _legs(monkeypatch, DEEP, thin)
    book_open = {"perp_buy_bps": 1.0, "spot_sell_bps": 5.0, "perp_sell_est_bps": 1.0,
                 "spot_buy_est_bps": 5.0, "borrow_stress": 3.0, "notional_usd": 500.0}
    pid = await _pred(sid, ctx={**CTX, "book_open": book_open}, hours_ago=0.5)
    notional = Decimal("500")
    async with shared_session_scope() as session:
        session.add(PaperPosition(
            id=uuid.uuid4(), prediction_id=pid, symbol=SYM, exchange="bybit", asset_class=ASSET,
            side="inverse_carry", notional_usd=notional,
            opened_at=datetime.now(timezone.utc) - timedelta(hours=0.5),
            opened_price=Decimal("100"), status="open", wallet_id=wallet_id,
        ))
    from backtest import paper_trade as PT
    async with shared_session_scope() as session:
        pos = (await session.execute(
            select(PaperPosition).where(PaperPosition.prediction_id == pid))).scalar_one()
        pred = await session.get(Prediction, pid)
    now = datetime.now(timezone.utc)
    mark = await PT._carry_mark_usd(pos, pred.context, now)
    assert await PT._close_position(pos, pred, "hit_horizon", now)
    async with shared_session_scope() as session:
        out = (await session.execute(select(Outcome).where(Outcome.prediction_id == pid))).scalar_one()
        pred = await session.get(Prediction, pid)
    bc = pred.context["book_close"]
    assert bc["close_source"] == "thin_book"
    spot_buy = (200 * 6.0 + 300 * (9.0 + CB.THIN_BOOK_PENALTY_BPS)) / 500
    assert bc["spot_buy_bps"] == pytest.approx(spot_buy, rel=1e-3)
    borrow = notional * Decimal("0.00002") * 3  # one started hour, entry x stress
    cost = Decimal(str(31.0 + 1 + 5 + 1 + spot_buy))
    assert out.pnl_usd == pytest.approx(-(notional * cost / 10000) - borrow, abs=Decimal("0.001"))
    assert mark <= out.pnl_usd + Decimal("0.000001")


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


# ── equity mark of an open carry (daily-loss circuit input) ─────────────────

def test_mark_cost_never_below_close_cost():
    """The mark takes each closing walk at the worse of the book now and the
    open-time estimate; a close charges one of the two."""
    _, book_open = CB.size_and_price_open(DEEP, ladder(100.0, 5.0, 10_000), 500, confirmed=False)
    for perp, spot in [(DEEP, DEEP), (DEEP, ladder(100.0, 20.0, 10_000)), (None, None), (DEEP, None)]:
        close = CB.close_cost_bps(book_open, perp, spot, 500)["total_bps"]
        assert CB.mark_cost_bps(book_open, perp, spot, 500) >= close - 1e-9
    # a book better than the estimate: the close pays 1 bps on the spot leg, the mark keeps 5
    assert CB.mark_cost_bps(book_open, DEEP, DEEP, 500) == pytest.approx(
        CB.close_cost_bps(book_open, DEEP, DEEP, 500)["total_bps"] + 4.0, rel=1e-6)


@pytest.mark.asyncio
@pytest.mark.parametrize("close_spot_half_spread", [1.0, 5.0, 20.0])
async def test_open_carry_equity_mark_not_above_realised_close(book_wallet, monkeypatch, close_spot_half_spread):
    """Funding settled so far (a 1 h-interval coin: three settlements in a
    2.5 h hold), minus four fees and four walks, minus borrow accrued: the
    wallet's unrealised for the carry is <= the PnL closing it books on the
    same books and the same instant. The old `hours/8 x live rate` mark had
    no costs at all."""
    from matrix_shared.models import TickerSnapshot
    from backtest import paper_trade as PT

    wallet_id, sid = book_wallet
    spot_now = ladder(100.0, close_spot_half_spread, 10_000)
    _legs(monkeypatch, DEEP, spot_now)
    book_open = {"perp_buy_bps": 1.0, "spot_sell_bps": 5.0, "perp_sell_est_bps": 1.0,
                 "spot_buy_est_bps": 5.0, "borrow_stress": 3.0, "notional_usd": 500.0}
    now = datetime.now(timezone.utc)
    opened = now - timedelta(hours=2.5)
    base = now.replace(minute=0, second=0, microsecond=0)
    settlements = [s for s in (base - timedelta(hours=k) for k in range(4)) if opened < s <= now - timedelta(minutes=2)]
    async with local_session_scope() as session:
        await session.execute(delete(TickerSnapshot).where(TickerSnapshot.symbol == SYM))
        for s in settlements:  # inverse_carry earns negative funding: 3 x 0.001
            session.add(TickerSnapshot(
                id=uuid.uuid4(), exchange="bybit", symbol=SYM, snapshot_ts=s - timedelta(minutes=1),
                last_price=Decimal("100"), mark_price=Decimal("100"), funding_rate=Decimal("-0.001"),
                next_funding_ts=s))
    try:
        pid = await _pred(sid, ctx={**CTX, "book_open": book_open}, hours_ago=2.5)
        notional = Decimal("500")
        async with shared_session_scope() as session:
            session.add(PaperPosition(
                id=uuid.uuid4(), prediction_id=pid, symbol=SYM, exchange="bybit", asset_class=ASSET,
                side="inverse_carry", notional_usd=notional, opened_at=opened,
                opened_price=Decimal("100"), status="open", wallet_id=wallet_id,
            ))
        async with shared_session_scope() as session:
            pos = (await session.execute(
                select(PaperPosition).where(PaperPosition.prediction_id == pid))).scalar_one()
            pred = await session.get(Prediction, pid)
            from matrix_shared.models import Wallet
            wallet = await session.get(Wallet, wallet_id)
            equity, unrealized, n_open = await PT._current_equity(session, wallet)
        mark = await PT._carry_mark_usd(pos, pred.context, now)
        assert n_open == 1 and unrealized == pytest.approx(mark, abs=Decimal("0.000001"))
        funding = notional * Decimal("0.001") * len(settlements)
        borrow = notional * Decimal("0.00002") * 3 * 3  # three started hours, entry x stress
        cost_bps = Decimal("31") + 1 + 5 + 1 + Decimal(str(max(5.0, close_spot_half_spread)))
        assert mark == pytest.approx(funding - notional * cost_bps / 10000 - borrow, abs=Decimal("0.0001"))

        assert await PT._close_position(pos, pred, "hit_horizon", now)
        async with shared_session_scope() as session:
            out = (await session.execute(select(Outcome).where(Outcome.prediction_id == pid))).scalar_one()
        assert mark <= out.pnl_usd + Decimal("0.000001")
        if close_spot_half_spread >= 5.0:  # book now at or worse than the estimate: the two agree
            assert mark == pytest.approx(out.pnl_usd, abs=Decimal("0.0001"))
        assert pos.id not in PT._CARRY_SETTLED and pos.id not in PT._CARRY_BORROW
    finally:
        async with local_session_scope() as session:
            await session.execute(delete(TickerSnapshot).where(TickerSnapshot.symbol == SYM))


# ── close loop never waits on REST ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_legs_for_close_bounded_when_rest_hangs(monkeypatch):
    import asyncio
    import time

    async def no_db(*a, **k):
        return None

    async def hang(*a, **k):
        await asyncio.sleep(30)

    monkeypatch.setattr(CB, "db_book", no_db)
    monkeypatch.setattr(CB, "rest_book", hang)
    t0 = time.monotonic()
    got = await CB.legs_for_close({"a": ("XUSDT", CTX), "b": ("YUSDT", CTX)}, timeout_s=0.3)
    assert time.monotonic() - t0 < 1.5
    assert got == {"a": (None, None), "b": (None, None)}

    async def db_perp_only(venue, category, symbol):
        return DEEP if category == "linear" else None

    monkeypatch.setattr(CB, "db_book", db_perp_only)
    got = await CB.legs_for_close({"a": ("XUSDT", CTX)}, timeout_s=0.3)
    assert got["a"] == (DEEP, None)  # the close prices the spot leg at the open estimate


@pytest.mark.asyncio
async def test_close_loop_closes_tpsl_before_and_without_waiting_on_carry_books(book_wallet, monkeypatch):
    """A due book-priced carry whose REST books hang: the directional TP close
    runs first, and the carry closes on whatever the bounded fetch returned."""
    import asyncio
    import time
    from backtest import paper_trade as PT

    wallet_id, sid = book_wallet

    async def no_db(*a, **k):
        return None

    async def hang(*a, **k):
        await asyncio.sleep(30)

    monkeypatch.setattr(CB, "db_book", no_db)
    monkeypatch.setattr(CB, "rest_book", hang)
    monkeypatch.setattr(CB, "CLOSE_BOOK_TIMEOUT_S", 0.3)

    book_open = {"perp_buy_bps": 1.0, "spot_sell_bps": 5.0, "perp_sell_est_bps": 1.0,
                 "spot_buy_est_bps": 5.0, "borrow_stress": 3.0, "notional_usd": 200.0}
    carry_pid = await _pred(sid, ctx={**CTX, "book_open": book_open}, hours_ago=49)  # past close_by
    dir_pid = uuid.uuid4()
    gen = datetime.now(timezone.utc)
    async with shared_session_scope() as session:
        session.add(Prediction(
            id=dir_pid, strategy_id=sid, strategy_version=1, asset_class=ASSET, symbol=SYM,
            exchange="bybit", side="long", confidence=Decimal("0.9"), horizon_seconds=3600,
            generated_at=gen, entry_price_ref=Decimal("90"), close_by=gen + timedelta(hours=1),
            status="open", tp_pct=Decimal("0.05"), context={}))
        await session.flush()
        for pid, side, px in ((carry_pid, "inverse_carry", "100"), (dir_pid, "long", "90")):
            session.add(PaperPosition(
                id=uuid.uuid4(), prediction_id=pid, symbol=SYM, exchange="bybit", asset_class=ASSET,
                side=side, notional_usd=Decimal("200"), opened_at=gen - timedelta(hours=1),
                opened_price=Decimal(px), status="open", wallet_id=wallet_id))

    calls: list[tuple] = []

    async def record(pos, pred, reason, now, *, force=False, books=None):
        calls.append((pred.id, reason, books, time.monotonic()))
        return False  # record only: never close (other open positions in the shared DB included)

    monkeypatch.setattr(PT, "_close_position", record)
    t0 = time.monotonic()
    await PT.close_due_positions()
    assert time.monotonic() - t0 < 10
    ours = {c[0]: c for c in calls if c[0] in (carry_pid, dir_pid)}
    assert ours[dir_pid][1] == "hit_tp" and ours[dir_pid][2] is None
    assert ours[carry_pid][1] == "hit_horizon" and ours[carry_pid][2] == (None, None)
    assert ours[dir_pid][3] < ours[carry_pid][3]  # the TP close did not queue behind the carry's books
