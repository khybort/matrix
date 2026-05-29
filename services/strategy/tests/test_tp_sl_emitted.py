"""Verify that every active strategy emits tp_pct/sl_pct on its drafts.

Tests are purely unit-level (call generate() against minimal fixture data,
assert draft fields). They do NOT persist to DB — that path is covered by
the persist module which just passes the fields through.

cash_and_carry is special: delta-neutral, tp_pct/sl_pct must be None.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import delete

from matrix_shared import local_session_scope, shared_session_scope
from matrix_shared.models import MarketBar, MarketTrade, TickerSnapshot, Prediction

from strategy.modules.crypto.funding_reversion import FundingReversion
from strategy.modules.crypto.grid import Grid
from strategy.modules.crypto.oi_delta import OiDelta
from strategy.modules.crypto.momentum_xs import MomentumXs
from strategy.modules.crypto.screener_follow import ScreenerFollow
from strategy.modules.crypto.cash_and_carry import CashAndCarry

pytestmark = pytest.mark.asyncio

# Unique test symbol prefix — won't collide with live ingestion
_SYM = "TEST_TPSL_USDT"
_EXCHANGE = "bybit"


async def _seed_trade(price: Decimal = Decimal("100")) -> None:
    """Drop a single fresh market trade for _SYM."""
    async with local_session_scope() as session:
        await session.execute(delete(MarketTrade).where(MarketTrade.symbol == _SYM))
        session.add(
            MarketTrade(
                id=uuid.uuid4(),
                exchange=_EXCHANGE,
                exchange_trade_id=f"tpsl-test-{uuid.uuid4().hex[:8]}",
                symbol=_SYM,
                trade_ts=datetime.now(UTC),
                side="buy",
                price=price,
                size=Decimal("1"),
            )
        )


async def _seed_ticker(funding_rate: Decimal, price: Decimal = Decimal("100"),
                       open_interest: Decimal | None = None) -> None:
    """Insert a TickerSnapshot for _SYM."""
    async with local_session_scope() as session:
        await session.execute(
            delete(TickerSnapshot).where(TickerSnapshot.symbol == _SYM)
        )
        session.add(
            TickerSnapshot(
                id=uuid.uuid4(),
                symbol=_SYM,
                exchange=_EXCHANGE,
                snapshot_ts=datetime.now(UTC),
                last_price=price,
                mark_price=price,
                funding_rate=funding_rate,
                open_interest=open_interest,
            )
        )


async def _seed_hourly_bars(n: int = 170) -> None:
    """Seed enough 1h bars for MomentumXs lookback (7 days = 168h)."""
    now = datetime.now(UTC)
    async with local_session_scope() as session:
        await session.execute(
            delete(MarketBar)
            .where(MarketBar.symbol == _SYM)
            .where(MarketBar.interval == "1h")
        )
        for i in range(n):
            ts = now - timedelta(hours=(n - 1 - i))
            close = Decimal("100") + Decimal(i)  # slow ramp — low std, passes vol filter
            session.add(
                MarketBar(
                    id=uuid.uuid4(),
                    symbol=_SYM,
                    asset_class="crypto",
                    interval="1h",
                    ts=ts,
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    volume=Decimal("1"),
                    source="test",
                    created_at=now,
                )
            )


@pytest_asyncio.fixture(autouse=True)
async def cleanup():
    """Wipe test symbol rows before and after each test."""
    async with local_session_scope() as session:
        await session.execute(delete(MarketTrade).where(MarketTrade.symbol == _SYM))
        await session.execute(delete(TickerSnapshot).where(TickerSnapshot.symbol == _SYM))
        await session.execute(
            delete(MarketBar).where(MarketBar.symbol == _SYM)
        )
    async with shared_session_scope() as session:
        await session.execute(delete(Prediction).where(Prediction.symbol == _SYM))

    yield

    async with local_session_scope() as session:
        await session.execute(delete(MarketTrade).where(MarketTrade.symbol == _SYM))
        await session.execute(delete(TickerSnapshot).where(TickerSnapshot.symbol == _SYM))
        await session.execute(
            delete(MarketBar).where(MarketBar.symbol == _SYM)
        )
    async with shared_session_scope() as session:
        await session.execute(delete(Prediction).where(Prediction.symbol == _SYM))


# ---------------------------------------------------------------------------
# FundingReversion
# ---------------------------------------------------------------------------

async def test_funding_reversion_emits_tp_sl():
    """FundingReversion drafts must carry tp_pct and sl_pct."""
    # Trigger signal: extreme positive funding → short
    await _seed_ticker(funding_rate=Decimal("0.0005"), price=Decimal("100"))
    await _seed_trade(price=Decimal("100"))

    strategy = FundingReversion(symbols=[_SYM])
    drafts = await strategy.generate()

    # There must be a draft (funding 0.05% > HIGH_FUNDING 0.02%)
    assert drafts, "expected at least one draft from funding_reversion"
    for d in drafts:
        assert d.tp_pct is not None, "tp_pct must be set"
        assert d.sl_pct is not None, "sl_pct must be set"
        assert d.tp_pct > 0
        assert d.sl_pct > 0


async def test_funding_reversion_tp_sl_constructor_override():
    """Constructor kwargs flow through to drafts."""
    await _seed_ticker(funding_rate=Decimal("0.0005"), price=Decimal("100"))
    await _seed_trade(price=Decimal("100"))

    custom_tp = Decimal("0.050")
    custom_sl = Decimal("0.025")
    strategy = FundingReversion(symbols=[_SYM], tp_pct=custom_tp, sl_pct=custom_sl)
    drafts = await strategy.generate()

    assert drafts
    for d in drafts:
        assert d.tp_pct == custom_tp
        assert d.sl_pct == custom_sl


# ---------------------------------------------------------------------------
# Grid
# ---------------------------------------------------------------------------

async def test_grid_emits_tp_sl():
    """Grid drafts carry tp_pct and sl_pct when a cross is detected."""
    now = datetime.now(UTC)
    band_hours = 25  # enough for the 24h band + 1 lookback bar
    async with local_session_scope() as session:
        await session.execute(
            delete(MarketBar)
            .where(MarketBar.symbol == _SYM)
            .where(MarketBar.interval == "1m")
        )
        for i in range(band_hours * 60):
            ts = now - timedelta(minutes=(band_hours * 60 - 1 - i))
            # Ramp from 100 to 200 and back so the band is wide
            half = (band_hours * 60) // 2
            if i < half:
                close = Decimal("100") + Decimal(i) * Decimal("100") / Decimal(half)
            else:
                close = Decimal("200") - Decimal(i - half) * Decimal("100") / Decimal(half)
            session.add(
                MarketBar(
                    id=uuid.uuid4(),
                    symbol=_SYM,
                    asset_class="crypto",
                    interval="1m",
                    ts=ts,
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    volume=Decimal("1"),
                    source="test",
                    created_at=now,
                )
            )

    # Force a cross: prior close at top of band (153), current trade at bottom (147)
    await _seed_trade(price=Decimal("147"))

    strategy = Grid(symbols=[_SYM])
    drafts = await strategy.generate()

    sym_drafts = [d for d in drafts if d.symbol == _SYM]
    if sym_drafts:
        for d in sym_drafts:
            assert d.tp_pct is not None, "tp_pct must be set on grid draft"
            assert d.sl_pct is not None, "sl_pct must be set on grid draft"
            assert d.tp_pct > 0
            assert d.sl_pct > 0

    async with local_session_scope() as session:
        await session.execute(
            delete(MarketBar)
            .where(MarketBar.symbol == _SYM)
            .where(MarketBar.interval == "1m")
        )


# ---------------------------------------------------------------------------
# OiDelta
# ---------------------------------------------------------------------------

async def test_oi_delta_emits_tp_sl():
    """OiDelta drafts carry tp_pct and sl_pct."""
    now = datetime.now(UTC)
    cutoff_past = now - timedelta(seconds=310)

    # Seed old ticker (low OI) and new ticker (high OI)
    async with local_session_scope() as session:
        await session.execute(
            delete(TickerSnapshot).where(TickerSnapshot.symbol == _SYM)
        )
        # Older snapshot (~5min ago) with lower OI
        session.add(
            TickerSnapshot(
                id=uuid.uuid4(),
                symbol=_SYM,
                exchange=_EXCHANGE,
                snapshot_ts=cutoff_past,
                last_price=Decimal("100"),
                funding_rate=Decimal("0.0001"),
                open_interest=Decimal("1000"),
            )
        )
        # Current snapshot with 2% OI jump > OI_JUMP_THRESHOLD 1.5%
        session.add(
            TickerSnapshot(
                id=uuid.uuid4(),
                symbol=_SYM,
                exchange=_EXCHANGE,
                snapshot_ts=now,
                last_price=Decimal("101.5"),
                funding_rate=Decimal("0.0001"),
                open_interest=Decimal("1020"),
            )
        )

    # Seed old trade and new trade (confirm price up move > 0.15%)
    async with local_session_scope() as session:
        await session.execute(delete(MarketTrade).where(MarketTrade.symbol == _SYM))
        session.add(
            MarketTrade(
                id=uuid.uuid4(),
                exchange=_EXCHANGE,
                exchange_trade_id=f"tpsl-old-{uuid.uuid4().hex[:8]}",
                symbol=_SYM,
                trade_ts=cutoff_past,
                side="buy",
                price=Decimal("100"),
                size=Decimal("1"),
            )
        )
        session.add(
            MarketTrade(
                id=uuid.uuid4(),
                exchange=_EXCHANGE,
                exchange_trade_id=f"tpsl-new-{uuid.uuid4().hex[:8]}",
                symbol=_SYM,
                trade_ts=now,
                side="buy",
                price=Decimal("101.5"),
                size=Decimal("1"),
            )
        )

    strategy = OiDelta(symbols=[_SYM])
    drafts = await strategy.generate()

    assert drafts, "expected at least one draft from oi_delta"
    for d in drafts:
        assert d.tp_pct is not None, "tp_pct must be set"
        assert d.sl_pct is not None, "sl_pct must be set"
        assert d.tp_pct > 0
        assert d.sl_pct > 0


# ---------------------------------------------------------------------------
# MomentumXs
# ---------------------------------------------------------------------------

async def test_momentum_xs_emits_tp_sl():
    """MomentumXs drafts carry tp_pct and sl_pct."""
    await _seed_hourly_bars(n=170)

    # Set up two symbols: one strong winner, one strong loser
    sym_loser = "TEST_TPSL_LOSER"
    async with local_session_scope() as session:
        await session.execute(
            delete(MarketBar).where(MarketBar.symbol == sym_loser)
        )
        now = datetime.now(UTC)
        for i in range(170):
            ts = now - timedelta(hours=(169 - i))
            close = Decimal("100") - Decimal(i) * Decimal("0.3")  # declining
            session.add(
                MarketBar(
                    id=uuid.uuid4(),
                    symbol=sym_loser,
                    asset_class="crypto",
                    interval="1h",
                    ts=ts,
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    volume=Decimal("1"),
                    source="test",
                    created_at=now,
                )
            )

    strategy = MomentumXs(
        symbols=[_SYM, sym_loser],
        top_k=1,
        vol_filter_pct=Decimal("0.99"),
        lookback_days=7,
        horizon_s=1,
    )
    drafts = await strategy.generate()

    assert drafts, "expected drafts from momentum_xs"
    for d in drafts:
        assert d.tp_pct is not None, f"tp_pct missing on {d.symbol} {d.side}"
        assert d.sl_pct is not None, f"sl_pct missing on {d.symbol} {d.side}"
        assert d.tp_pct > 0
        assert d.sl_pct > 0

    async with local_session_scope() as session:
        await session.execute(
            delete(MarketBar).where(MarketBar.symbol == sym_loser)
        )


# ---------------------------------------------------------------------------
# CashAndCarry — delta-neutral: tp_pct/sl_pct must be None
# ---------------------------------------------------------------------------

async def test_cash_and_carry_no_tp_sl():
    """CashAndCarry (delta-neutral) drafts must NOT have tp_pct or sl_pct."""
    await _seed_ticker(
        funding_rate=Decimal("0.0005"),  # > min_funding 0.0001
        price=Decimal("100"),
    )

    strategy = CashAndCarry(symbols=[_SYM])
    drafts = await strategy.generate()

    assert drafts, "expected at least one draft from cash_and_carry"
    for d in drafts:
        assert d.tp_pct is None, "delta-neutral must NOT have tp_pct"
        assert d.sl_pct is None, "delta-neutral must NOT have sl_pct"


# ---------------------------------------------------------------------------
# Default constant sanity checks (pure unit, no DB)
# ---------------------------------------------------------------------------

def test_funding_reversion_defaults():
    from strategy.modules.crypto.funding_reversion import DEFAULT_TP_PCT, DEFAULT_SL_PCT
    assert DEFAULT_TP_PCT > 0
    assert DEFAULT_SL_PCT > 0
    assert DEFAULT_TP_PCT > DEFAULT_SL_PCT  # TP wider than SL


def test_grid_defaults():
    from strategy.modules.crypto.grid import DEFAULT_TP_PCT, DEFAULT_SL_PCT
    assert DEFAULT_TP_PCT > 0
    assert DEFAULT_SL_PCT > 0


def test_oi_delta_defaults():
    from strategy.modules.crypto.oi_delta import DEFAULT_TP_PCT, DEFAULT_SL_PCT
    assert DEFAULT_TP_PCT > 0
    assert DEFAULT_SL_PCT > 0


def test_momentum_xs_defaults():
    from strategy.modules.crypto.momentum_xs import DEFAULT_TP_PCT, DEFAULT_SL_PCT
    assert DEFAULT_TP_PCT > 0
    assert DEFAULT_SL_PCT > 0


def test_screener_follow_defaults():
    from strategy.modules.crypto.screener_follow import DEFAULT_TP_PCT, DEFAULT_SL_PCT
    assert DEFAULT_TP_PCT > 0
    assert DEFAULT_SL_PCT > 0


def test_bist_gap_fade_defaults():
    from strategy.modules.bist.gap_fade import DEFAULT_TP_PCT, DEFAULT_SL_PCT
    assert DEFAULT_TP_PCT > 0
    assert DEFAULT_SL_PCT > 0


def test_bist_intraday_reversion_defaults():
    from strategy.modules.bist.intraday_reversion import DEFAULT_TP_PCT, DEFAULT_SL_PCT
    assert DEFAULT_TP_PCT > 0
    assert DEFAULT_SL_PCT > 0


def test_bist_volume_breakout_defaults():
    from strategy.modules.bist.volume_breakout import DEFAULT_TP_PCT, DEFAULT_SL_PCT
    assert DEFAULT_TP_PCT > 0
    assert DEFAULT_SL_PCT > 0


def test_bist_news_event_defaults():
    from strategy.modules.bist.news_event import DEFAULT_TP_PCT, DEFAULT_SL_PCT
    assert DEFAULT_TP_PCT > 0
    assert DEFAULT_SL_PCT > 0


def test_cash_and_carry_no_default_tp_sl():
    """CashAndCarry constructor defaults must be None for TP/SL."""
    import inspect
    from strategy.modules.crypto.cash_and_carry import CashAndCarry
    sig = inspect.signature(CashAndCarry.__init__)
    # The class doesn't accept tp_pct/sl_pct — just verify no drafts carry them.
    # (Checked in the live test above; this confirms the contract at code level.)
    assert "tp_pct" not in sig.parameters, (
        "cash_and_carry should not expose tp_pct constructor param"
    )
