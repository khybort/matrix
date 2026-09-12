"""metrics_window must scope by asset_class — a strategy_id shared across
markets (e.g. matrix_agent in crypto + bist) must not mix their PnL, and a
BIST strategy's proposal must be written against the BIST config
(docs/AUTONOMY_PLAN.md §5 bug #3).
"""

from __future__ import annotations

import uuid

import pytest

from reflection.metrics import metrics_window

pytestmark = pytest.mark.asyncio


async def test_metrics_window_scopes_by_asset_class(seed_outcomes, cert_cleanup):
    sid = f"ac_scope_{uuid.uuid4().hex[:6]}"
    cert_cleanup.append((sid, "crypto", 1))
    await seed_outcomes(sid, "crypto", 1, n=10, win_rate=1.0, pnl_per_trade_usd=2.0)
    await seed_outcomes(sid, "bist", 1, n=4, win_rate=0.0, pnl_per_trade_usd=5.0)

    crypto = await metrics_window(sid, 1, asset_class="crypto")
    bist = await metrics_window(sid, 1, asset_class="bist")
    mixed = await metrics_window(sid, 1)  # legacy: no filter

    assert crypto.n_outcomes == 10 and crypto.total_pnl_usd == 20
    assert crypto.asset_class == "crypto"
    assert bist.n_outcomes == 4 and bist.total_pnl_usd == -20
    assert bist.asset_class == "bist"
    assert mixed.n_outcomes == 14
