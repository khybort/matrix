"""_apply_setup_memory: bumps/halves confidence, never flips side, skips holds
and exploration trades; the LLM prompt lists both sides' neighbours."""

from __future__ import annotations

from decimal import Decimal

import pytest

from agent import decide as D
from matrix_shared.setup_memory import EMPTY, SetupStats
from matrix_shared.stats import wilson_bounds

pytestmark = pytest.mark.asyncio


class _F:
    symbol = "BTCUSDT"
    last_price = Decimal("100")
    n_trades_60s = 10
    buy_share_60s = Decimal("0.6")
    notional_60s_usd = Decimal("1000")
    spread_bps = Decimal("5")
    bid_ask_imbalance_top5 = Decimal("0.5")
    funding_rate = Decimal("0.0001")
    open_interest = Decimal("1")
    oi_delta_pct_5m = Decimal("0")
    price_change_pct_5m = Decimal("0.001")
    n_news_1h = 0
    news_titles_sample: list[str] = []
    graph_mention_count = 0
    graph_recency_weight = Decimal("0")
    graph_direct_polarity = Decimal("0")
    graph_contextual_polarity = Decimal("0")
    graph_related_companies: list[str] = []
    graph_co_mentioned_assets: list[str] = []
    regime = "low/flat/neutral"


def _stats(wins: int, n: int, pnl: float) -> SetupStats:
    lo, hi = wilson_bounds(wins, n)
    return SetupStats(n, wins, wins / n, lo, hi, pnl, 0.93, 100)


def _decision(side="long", conf="0.6", explore=False):
    return D.Decision(symbol="BTCUSDT", side=side, confidence=Decimal(conf), thesis="t", method="rule",
                      feature_dump={"is_exploration": explore}, last_price=Decimal("100"))


def _patch(monkeypatch, stats):
    seen = []

    async def fake(**kw):
        seen.append(kw)
        return stats
    monkeypatch.setattr(D, "similar_setups", fake)
    monkeypatch.setattr("matrix_shared.setup_memory.MIN_N", 10)
    return seen


async def test_bad_memory_halves_confidence_keeps_side(monkeypatch):
    seen = _patch(monkeypatch, _stats(2, 15, -0.004))
    d = await D._apply_setup_memory(_decision(), _F(), "matrix_agent", asset_class="crypto")
    assert d.side == "long" and d.confidence == Decimal("0.3") and d.method == "rule+setup"
    assert d.feature_dump["setup_memory"]["verdict"] == "bad" and d.thesis.startswith("SETUP-")
    assert seen[0]["side"] == "long" and seen[0]["strategy_id"] == "matrix_agent"


async def test_good_memory_bumps_confidence(monkeypatch):
    _patch(monkeypatch, _stats(14, 16, 0.003))
    d = await D._apply_setup_memory(_decision(conf="0.5"), _F(), "matrix_agent", asset_class="crypto")
    assert d.confidence == Decimal("0.60") and d.thesis.startswith("SETUP+")


async def test_neutral_memory_is_audited_but_unchanged(monkeypatch):
    _patch(monkeypatch, _stats(8, 15, 0.0001))
    d = await D._apply_setup_memory(_decision(), _F(), "matrix_agent", asset_class="crypto")
    assert d.confidence == Decimal("0.6") and d.method == "rule"
    assert d.feature_dump["setup_memory"]["n"] == 15


async def test_holds_exploration_and_empty_are_untouched(monkeypatch):
    seen = _patch(monkeypatch, _stats(2, 15, -0.004))
    hold = await D._apply_setup_memory(_decision(side="hold"), _F(), "matrix_agent", asset_class="crypto")
    explore = await D._apply_setup_memory(_decision(explore=True), _F(), "matrix_agent", asset_class="crypto")
    assert hold.confidence == Decimal("0.6") and explore.confidence == Decimal("0.6") and seen == []
    _patch(monkeypatch, EMPTY)
    d = await D._apply_setup_memory(_decision(), _F(), "matrix_agent", asset_class="crypto")
    assert "setup_memory" not in d.feature_dump


def test_llm_prompt_lists_neighbours_per_side():
    setups = {"long": _stats(12, 15, 0.002), "short": EMPTY}
    p = D._llm_prompt(_F(), setups=setups)
    assert "Nearest past setups" in p and "LONG: n=15" in p and "SHORT" not in p
    assert "Nearest past setups" not in D._llm_prompt(_F(), setups={"long": EMPTY, "short": EMPTY})
