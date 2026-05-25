"""execution.safety.should_submit_live — gate must deny in every bad state.

Each test isolates one failure mode so a regression in any single layer
fails loudly rather than passing silently because another layer happens
to catch the bad call. The happy path is also covered, so we know the
gate doesn't deny everything indiscriminately.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import delete, update

from matrix_shared import shared_session_scope
from matrix_shared.models import PaperPosition, Wallet

from execution.safety import should_submit_live

pytestmark = pytest.mark.asyncio


# --- failure-mode tests -------------------------------------------------

async def test_denies_when_no_certificate(wallet_id, monkeypatch):
    """No cert in the DB at all → gate must deny."""
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("LIVE_CAPITAL_CAP_USD", "2000")
    decision = await should_submit_live(
        strategy_id="never_granted_strat",
        asset_class="crypto",
        strategy_version=1,
        intended_notional_usd=Decimal("100"),
        wallet_id=wallet_id,
    )
    assert decision.allowed is False
    assert any("certificate" in r for r in decision.reasons), decision.reasons


async def test_denies_when_live_execution_flag_is_false(wallet_id, grant_cert, monkeypatch):
    """Cert exists, wallet healthy, but env flag off → gate denies."""
    sid, ac, ver = await grant_cert()
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("LIVE_CAPITAL_CAP_USD", "2000")
    decision = await should_submit_live(
        strategy_id=sid, asset_class=ac, strategy_version=ver,
        intended_notional_usd=Decimal("100"),
        wallet_id=wallet_id,
    )
    assert decision.allowed is False
    assert any("LIVE_EXECUTION_ENABLED" in r for r in decision.reasons), decision.reasons


async def test_denies_when_circuit_tripped(wallet_id, grant_cert, monkeypatch):
    """Daily-loss circuit breaker overrides everything else."""
    sid, ac, ver = await grant_cert()
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("LIVE_CAPITAL_CAP_USD", "2000")
    async with shared_session_scope() as session:
        await session.execute(
            update(Wallet)
            .where(Wallet.id == wallet_id)
            .values(circuit_tripped_at=datetime.now(timezone.utc))
        )
    decision = await should_submit_live(
        strategy_id=sid, asset_class=ac, strategy_version=ver,
        intended_notional_usd=Decimal("100"),
        wallet_id=wallet_id,
    )
    assert decision.allowed is False
    assert any("circuit" in r for r in decision.reasons), decision.reasons


async def test_denies_when_notional_exceeds_position_cap(wallet_id, grant_cert, monkeypatch):
    """Default max_position_pct=0.02 of $10k = $200 cap; ask for $500."""
    sid, ac, ver = await grant_cert()
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("LIVE_CAPITAL_CAP_USD", "2000")
    decision = await should_submit_live(
        strategy_id=sid, asset_class=ac, strategy_version=ver,
        intended_notional_usd=Decimal("500"),
        wallet_id=wallet_id,
    )
    assert decision.allowed is False
    assert any("max_position_pct" in r for r in decision.reasons), decision.reasons


async def test_denies_when_concurrent_positions_at_cap(wallet_id, grant_cert, monkeypatch):
    """5 already-open positions → gate refuses a 6th."""
    sid, ac, ver = await grant_cert()
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("LIVE_CAPITAL_CAP_USD", "2000")

    # Need predictions to satisfy paper_positions.prediction_id FK constraint.
    # Two-phase: commit Predictions first so FK targets exist, then Positions.
    # (One session works in theory but the topo-sort wasn't reliable here.)
    from matrix_shared.models import Prediction
    pred_ids: list[uuid.UUID] = []
    async with shared_session_scope() as session:
        for i in range(5):
            pid = uuid.uuid4()
            pred_ids.append(pid)
            session.add(
                Prediction(
                    id=pid, strategy_id=sid, strategy_version=ver, asset_class=ac,
                    symbol="BTCUSDT", exchange="bybit", side="long",
                    confidence=Decimal("0.5"), horizon_seconds=60,
                    generated_at=datetime.now(timezone.utc),
                    close_by=datetime.now(timezone.utc),
                    entry_price_ref=Decimal("100"),
                    status="open",
                )
            )
    async with shared_session_scope() as session:
        for pid in pred_ids:
            session.add(
                PaperPosition(
                    id=uuid.uuid4(), prediction_id=pid, symbol="BTCUSDT",
                    exchange="bybit", side="long", notional_usd=Decimal("50"),
                    opened_at=datetime.now(timezone.utc),
                    opened_price=Decimal("100"),
                    status="open", wallet_id=wallet_id, asset_class=ac,
                )
            )

    try:
        decision = await should_submit_live(
            strategy_id=sid, asset_class=ac, strategy_version=ver,
            intended_notional_usd=Decimal("100"),
            wallet_id=wallet_id,
        )
        assert decision.allowed is False
        assert any("positions open" in r for r in decision.reasons), decision.reasons
    finally:
        # Clean up the 5 positions + their predictions
        async with shared_session_scope() as session:
            await session.execute(delete(PaperPosition).where(PaperPosition.wallet_id == wallet_id))
            await session.execute(delete(Prediction).where(Prediction.strategy_id == sid))


async def test_denies_when_capital_cap_exceeded(wallet_id, grant_cert, monkeypatch):
    """LIVE_CAPITAL_CAP_USD=100 ceiling, asking for $150."""
    sid, ac, ver = await grant_cert()
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("LIVE_CAPITAL_CAP_USD", "100")
    decision = await should_submit_live(
        strategy_id=sid, asset_class=ac, strategy_version=ver,
        intended_notional_usd=Decimal("150"),
        wallet_id=wallet_id,
    )
    assert decision.allowed is False
    assert any("LIVE_CAPITAL_CAP_USD" in r for r in decision.reasons), decision.reasons


async def test_denies_when_certificate_expired(wallet_id, grant_cert, monkeypatch):
    """Cert with validity_until already in the past → gate treats as missing."""
    sid, ac, ver = await grant_cert(validity_hours=-1)  # expired
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("LIVE_CAPITAL_CAP_USD", "2000")
    decision = await should_submit_live(
        strategy_id=sid, asset_class=ac, strategy_version=ver,
        intended_notional_usd=Decimal("50"),
        wallet_id=wallet_id,
    )
    assert decision.allowed is False
    assert any("certificate" in r for r in decision.reasons), decision.reasons


# --- happy path ---------------------------------------------------------

async def test_allows_when_every_gate_is_green(wallet_id, grant_cert, monkeypatch):
    """All checks pass → allowed=True, no reasons."""
    sid, ac, ver = await grant_cert(validity_hours=24)
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("LIVE_CAPITAL_CAP_USD", "2000")
    decision = await should_submit_live(
        strategy_id=sid, asset_class=ac, strategy_version=ver,
        intended_notional_usd=Decimal("100"),  # well under $200 (2% of $10k)
        wallet_id=wallet_id,
    )
    assert decision.allowed is True, decision.reasons
    assert decision.reasons == []
