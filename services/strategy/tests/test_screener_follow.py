"""ScreenerFollow strategy unit tests.

Each test seeds screener_signals into LOCAL DB using raw SQL (no ORM model),
runs generate(), then asserts on the PredictionDraft list. All signals use a
synthetic symbol (TEST_SCREENER_SYM) to avoid colliding with live data.

Cooldown state lives in the SHARED predictions table (same DB in tests) —
see conftest.py for DSN wiring.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, text

from matrix_shared import local_session_scope, shared_session_scope
from matrix_shared.models import MarketTrade, Prediction

from strategy.modules.crypto.screener_follow import STRATEGY_ID, ScreenerFollow

from tests.conftest import TEST_SYM

# Separate synthetic symbol so screener_signals don't bleed into other tests.
TEST_SCREENER_SYM = "TEST_SCRNUSDT"

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _insert_signal(
    *,
    symbol: str = TEST_SCREENER_SYM,
    signal_type: str = "funding_extreme",
    funding_rate: float = 0.001,
    score: float = 0.10,
    passes: int = 3,
    status: str = "candidate",
    observed_at: datetime | None = None,
) -> None:
    if observed_at is None:
        observed_at = datetime.now(UTC)
    async with local_session_scope() as session:
        await session.execute(
            text("""
                INSERT INTO screener_signals
                    (symbol, signal_type, funding_rate, oi_value, score, passes, status, observed_at)
                VALUES
                    (:symbol, :signal_type, :funding_rate, NULL, :score, :passes, :status, :observed_at)
                ON CONFLICT (symbol, signal_type)
                DO UPDATE SET
                    funding_rate = EXCLUDED.funding_rate,
                    score        = EXCLUDED.score,
                    passes       = EXCLUDED.passes,
                    status       = EXCLUDED.status,
                    observed_at  = EXCLUDED.observed_at
            """),
            {
                "symbol": symbol,
                "signal_type": signal_type,
                "funding_rate": funding_rate,
                "score": score,
                "passes": passes,
                "status": status,
                "observed_at": observed_at,
            },
        )


async def _insert_trade(
    *,
    symbol: str = TEST_SCREENER_SYM,
    price: Decimal = Decimal("100"),
) -> None:
    now = datetime.now(UTC)
    async with local_session_scope() as session:
        session.add(
            MarketTrade(
                id=uuid.uuid4(),
                exchange="bybit",
                exchange_trade_id=f"scrntest-{uuid.uuid4().hex[:8]}",
                symbol=symbol,
                trade_ts=now,
                side="buy",
                price=price,
                size=Decimal("1"),
            )
        )


async def _cleanup() -> None:
    async with local_session_scope() as session:
        await session.execute(
            text("DELETE FROM screener_signals WHERE symbol = :sym"),
            {"sym": TEST_SCREENER_SYM},
        )
        await session.execute(
            delete(MarketTrade).where(MarketTrade.symbol == TEST_SCREENER_SYM)
        )
    async with shared_session_scope() as session:
        await session.execute(
            delete(Prediction).where(Prediction.symbol == TEST_SCREENER_SYM)
        )


def _strategy() -> ScreenerFollow:
    """Isolated instance targeting only the test symbol."""
    return ScreenerFollow(symbols=[TEST_SCREENER_SYM])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_funding_extreme_positive_emits_short() -> None:
    """Very positive funding → longs crowded → strategy emits side='short'."""
    try:
        await _insert_signal(
            signal_type="funding_extreme",
            funding_rate=0.001,  # strongly positive
            score=0.10,
            passes=3,
        )
        await _insert_trade()

        drafts = await _strategy().generate()
        sym_drafts = [d for d in drafts if d.symbol == TEST_SCREENER_SYM]

        assert len(sym_drafts) == 1, f"expected 1 draft, got {len(sym_drafts)}"
        d = sym_drafts[0]
        assert d.side == "short"
        assert d.strategy_id == STRATEGY_ID
        assert d.context["signal_type"] == "funding_extreme"
    finally:
        await _cleanup()


async def test_oi_spike_negative_funding_emits_short() -> None:
    """OI spike + negative funding rate → longs crowded → squeeze DOWN → SHORT."""
    try:
        await _insert_signal(
            signal_type="oi_spike",
            funding_rate=-0.0005,  # negative: longs paying shorts
            score=0.08,
            passes=4,
        )
        await _insert_trade()

        drafts = await _strategy().generate()
        sym_drafts = [d for d in drafts if d.symbol == TEST_SCREENER_SYM]

        assert len(sym_drafts) == 1, f"expected 1 draft, got {len(sym_drafts)}"
        assert sym_drafts[0].side == "short"
    finally:
        await _cleanup()


async def test_top_funding_skipped() -> None:
    """signal_type='top_funding' is informational only — must produce no draft."""
    try:
        await _insert_signal(
            signal_type="top_funding",
            funding_rate=0.002,
            score=0.05,
            passes=5,
        )
        await _insert_trade()

        drafts = await _strategy().generate()
        sym_drafts = [d for d in drafts if d.symbol == TEST_SCREENER_SYM]

        assert sym_drafts == [], "top_funding signal should not produce a draft"
    finally:
        await _cleanup()


async def test_low_score_filtered() -> None:
    """Candidate with score < min_score must not produce a draft."""
    try:
        strat = ScreenerFollow(
            symbols=[TEST_SCREENER_SYM],
            min_score=Decimal("0.10"),
        )
        await _insert_signal(
            signal_type="funding_extreme",
            funding_rate=0.001,
            score=0.02,  # below 0.10 min_score
            passes=3,
        )
        await _insert_trade()

        drafts = await strat.generate()
        sym_drafts = [d for d in drafts if d.symbol == TEST_SCREENER_SYM]

        assert sym_drafts == [], "low-score signal should be filtered out"
    finally:
        await _cleanup()


async def test_cooldown_blocks_repeat() -> None:
    """First generate() emits a draft; immediate second call returns empty due
    to cooldown on the same (symbol, signal_type) within horizon_s."""
    try:
        await _insert_signal(
            signal_type="funding_extreme",
            funding_rate=0.001,
            score=0.10,
            passes=3,
        )
        await _insert_trade()

        strat = _strategy()

        # First call — should produce a draft.
        first = await strat.generate()
        first_sym = [d for d in first if d.symbol == TEST_SCREENER_SYM]
        assert len(first_sym) == 1, f"first call: expected 1 draft, got {len(first_sym)}"

        # Persist the draft as a Prediction to simulate the dispatcher storing it.
        d = first_sym[0]
        async with shared_session_scope() as session:
            session.add(
                Prediction(
                    id=d.id,
                    strategy_id=d.strategy_id,
                    strategy_version=d.strategy_version,
                    generated_at=d.generated_at,
                    symbol=d.symbol,
                    exchange=d.exchange,
                    asset_class="crypto",
                    side=d.side,
                    confidence=d.confidence,
                    horizon_seconds=d.horizon_seconds,
                    close_by=d.generated_at + timedelta(seconds=d.horizon_seconds),
                    entry_price_ref=d.entry_price_ref,
                    thesis=d.thesis,
                    context=d.context,
                    status="open",
                )
            )

        # Second call — cooldown must suppress re-emission.
        second = await strat.generate()
        second_sym = [s for s in second if s.symbol == TEST_SCREENER_SYM]
        assert second_sym == [], f"second call: expected 0 drafts, got {len(second_sym)}"
    finally:
        await _cleanup()
