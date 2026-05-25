"""Auto-grant cert pipeline (matrix_shared.trading_safety.maybe_grant_certificate).

Tests run against matrix-postgres-shared (port 5433). Eligibility kwargs are
loosened in the tests so 10 outcomes / 0 observation days is enough — full
defaults from docs/TRADING.md would need 60 days of paper-trade history.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from matrix_shared import maybe_grant_certificate, shared_session_scope
from matrix_shared.models import PaperTradeCertificate

pytestmark = pytest.mark.asyncio


# Loosen thresholds so the test fixtures (10 outcomes, same day) qualify.
_LOOSE = dict(
    min_observation_days=0,
    min_outcomes=5,
    min_win_rate=Decimal("0.0"),
    min_total_pnl_usd=Decimal("-100"),
    max_drawdown_pct=Decimal("1.0"),
)


async def _fetch(strategy_id: str, asset_class: str, version: int) -> PaperTradeCertificate | None:
    async with shared_session_scope() as session:
        stmt = (
            select(PaperTradeCertificate)
            .where(PaperTradeCertificate.strategy_id == strategy_id)
            .where(PaperTradeCertificate.asset_class == asset_class)
            .where(PaperTradeCertificate.version == version)
        )
        return (await session.execute(stmt)).scalar_one_or_none()


async def test_grants_when_eligible(seed_outcomes, cert_cleanup):
    sid = f"grant_a_{uuid.uuid4().hex[:6]}"
    ac, ver = "crypto", 1
    cert_cleanup.append((sid, ac, ver))
    await seed_outcomes(sid, ac, ver, n=10, win_rate=0.6)

    ok, verdict, reason = await maybe_grant_certificate(sid, ac, ver, **_LOOSE)
    assert ok is True, (reason, verdict)
    row = await _fetch(sid, ac, ver)
    assert row is not None
    assert row.status == "granted"
    assert row.validity_until is not None
    assert row.validity_until > datetime.now(timezone.utc)
    assert row.granted_by == "auto-eligibility"
    assert row.n_outcomes == 10


async def test_does_not_double_grant(seed_outcomes, cert_cleanup):
    sid = f"grant_b_{uuid.uuid4().hex[:6]}"
    ac, ver = "crypto", 1
    cert_cleanup.append((sid, ac, ver))
    await seed_outcomes(sid, ac, ver, n=10, win_rate=0.6)

    ok1, _, _ = await maybe_grant_certificate(sid, ac, ver, **_LOOSE)
    assert ok1 is True
    ok2, verdict2, reason2 = await maybe_grant_certificate(sid, ac, ver, **_LOOSE)
    assert ok2 is False
    assert reason2 == "already granted"
    assert verdict2 is None


async def test_upgrades_pending_to_granted(seed_outcomes, cert_cleanup):
    sid = f"grant_c_{uuid.uuid4().hex[:6]}"
    ac, ver = "crypto", 1
    cert_cleanup.append((sid, ac, ver))
    await seed_outcomes(sid, ac, ver, n=10, win_rate=0.6)
    # Pre-seed a 'pending' cert
    async with shared_session_scope() as session:
        session.add(PaperTradeCertificate(
            strategy_id=sid, asset_class=ac, version=ver, status="pending",
        ))

    ok, _, _ = await maybe_grant_certificate(sid, ac, ver, **_LOOSE)
    assert ok is True
    row = await _fetch(sid, ac, ver)
    assert row.status == "granted"


async def test_regrants_after_expiry(seed_outcomes, cert_cleanup):
    sid = f"grant_d_{uuid.uuid4().hex[:6]}"
    ac, ver = "crypto", 1
    cert_cleanup.append((sid, ac, ver))
    await seed_outcomes(sid, ac, ver, n=10, win_rate=0.6)
    # Pre-seed an EXPIRED granted cert (validity_until in the past)
    async with shared_session_scope() as session:
        session.add(PaperTradeCertificate(
            strategy_id=sid, asset_class=ac, version=ver,
            status="granted",
            granted_at=datetime.now(timezone.utc) - timedelta(days=30),
            validity_until=datetime.now(timezone.utc) - timedelta(days=1),
        ))

    ok, _, _ = await maybe_grant_certificate(sid, ac, ver, **_LOOSE)
    assert ok is True
    row = await _fetch(sid, ac, ver)
    assert row.status == "granted"
    assert row.validity_until > datetime.now(timezone.utc)


async def test_skips_when_not_eligible(seed_outcomes, cert_cleanup):
    sid = f"grant_e_{uuid.uuid4().hex[:6]}"
    ac, ver = "crypto", 1
    cert_cleanup.append((sid, ac, ver))
    await seed_outcomes(sid, ac, ver, n=3, win_rate=0.6)  # n < min_outcomes=5

    ok, verdict, reason = await maybe_grant_certificate(sid, ac, ver, **_LOOSE)
    assert ok is False
    assert reason == "not eligible"
    assert verdict is not None
    assert "n_outcomes" in " ".join(verdict.reasons)
    row = await _fetch(sid, ac, ver)
    assert row is None
