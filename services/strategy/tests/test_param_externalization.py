"""Verify that funding_reversion and BIST strategies accept externalized params.

Tests are pure unit tests — no DB required. Each strategy's generate() is
NOT called (it needs a live DB); instead we verify:
  1. Default instantiation works (module-level constants flow through).
  2. Custom kwargs override the defaults (threshold honored on the instance).

The generate() path is exercised by the DB-backed integration tests; these
tests lock in the constructor contract so regressions are caught immediately.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from strategy.modules.crypto.funding_reversion import (
    FUNDING_CAP,
    HIGH_FUNDING,
    HORIZON_S as FR_HORIZON_S,
    FundingReversion,
)
from strategy.modules.bist.gap_fade import (
    GAP_CAP,
    GAP_THRESHOLD,
    HORIZON_S as GF_HORIZON_S,
    BistGapFade,
)
from strategy.modules.bist.intraday_reversion import (
    DROP_CAP,
    DROP_THRESHOLD,
    HORIZON_S as IR_HORIZON_S,
    BistIntradayReversion,
)
from strategy.modules.bist.volume_breakout import (
    VOL_MULT,
    VOL_MULT_CAP,
    HORIZON_S as VB_HORIZON_S,
    BistVolumeBreakout,
)


# ---------------------------------------------------------------------------
# FundingReversion
# ---------------------------------------------------------------------------

class TestFundingReversionParams:
    def test_defaults(self):
        strat = FundingReversion()
        assert strat.high_funding == HIGH_FUNDING
        assert strat.funding_cap == FUNDING_CAP
        assert strat.horizon_seconds == FR_HORIZON_S

    def test_custom_high_funding(self):
        strat = FundingReversion(high_funding=Decimal("0.0005"))
        assert strat.high_funding == Decimal("0.0005")
        # Other defaults unchanged
        assert strat.funding_cap == FUNDING_CAP
        assert strat.horizon_seconds == FR_HORIZON_S

    def test_custom_funding_cap(self):
        strat = FundingReversion(funding_cap=Decimal("0.001"))
        assert strat.funding_cap == Decimal("0.001")

    def test_custom_horizon_s(self):
        strat = FundingReversion(horizon_s=1200)
        assert strat.horizon_seconds == 1200

    def test_all_custom(self):
        strat = FundingReversion(
            high_funding=Decimal("0.0003"),
            funding_cap=Decimal("0.0008"),
            horizon_s=300,
        )
        assert strat.high_funding == Decimal("0.0003")
        assert strat.funding_cap == Decimal("0.0008")
        assert strat.horizon_seconds == 300

    def test_threshold_honored(self):
        """Custom high_funding is stored and would filter signals in generate()."""
        tight = FundingReversion(high_funding=Decimal("0.0010"))
        loose = FundingReversion(high_funding=Decimal("0.00005"))
        # The threshold should be stored as instance attribute so generate()
        # compares abs(fr) against self.high_funding, not the module constant.
        assert tight.high_funding > loose.high_funding


# ---------------------------------------------------------------------------
# BistGapFade
# ---------------------------------------------------------------------------

class TestBistGapFadeParams:
    def test_defaults(self):
        strat = BistGapFade()
        assert strat.gap_threshold == GAP_THRESHOLD
        assert strat.gap_cap == GAP_CAP
        assert strat.horizon_seconds == GF_HORIZON_S
        assert strat.dedup_window_s == GF_HORIZON_S  # dedup mirrors horizon

    def test_custom_gap_threshold(self):
        strat = BistGapFade(gap_threshold=Decimal("0.02"))
        assert strat.gap_threshold == Decimal("0.02")
        assert strat.gap_cap == GAP_CAP

    def test_custom_gap_cap(self):
        strat = BistGapFade(gap_cap=Decimal("0.10"))
        assert strat.gap_cap == Decimal("0.10")

    def test_custom_horizon_s(self):
        strat = BistGapFade(horizon_s=900)
        assert strat.horizon_seconds == 900
        # dedup window tracks horizon
        assert strat.dedup_window_s == 900

    def test_all_custom(self):
        strat = BistGapFade(
            gap_threshold=Decimal("0.025"),
            gap_cap=Decimal("0.08"),
            horizon_s=600,
        )
        assert strat.gap_threshold == Decimal("0.025")
        assert strat.gap_cap == Decimal("0.08")
        assert strat.horizon_seconds == 600

    def test_threshold_honored(self):
        tight = BistGapFade(gap_threshold=Decimal("0.04"))
        loose = BistGapFade(gap_threshold=Decimal("0.005"))
        assert tight.gap_threshold > loose.gap_threshold


# ---------------------------------------------------------------------------
# BistIntradayReversion
# ---------------------------------------------------------------------------

class TestBistIntradayReversionParams:
    def test_defaults(self):
        strat = BistIntradayReversion()
        assert strat.drop_threshold == DROP_THRESHOLD
        assert strat.drop_cap == DROP_CAP
        assert strat.horizon_seconds == IR_HORIZON_S

    def test_custom_drop_threshold(self):
        strat = BistIntradayReversion(drop_threshold=Decimal("0.05"))
        assert strat.drop_threshold == Decimal("0.05")
        assert strat.drop_cap == DROP_CAP

    def test_custom_drop_cap(self):
        strat = BistIntradayReversion(drop_cap=Decimal("0.10"))
        assert strat.drop_cap == Decimal("0.10")

    def test_custom_horizon_s(self):
        strat = BistIntradayReversion(horizon_s=1800)
        assert strat.horizon_seconds == 1800

    def test_all_custom(self):
        strat = BistIntradayReversion(
            drop_threshold=Decimal("0.04"),
            drop_cap=Decimal("0.09"),
            horizon_s=2400,
        )
        assert strat.drop_threshold == Decimal("0.04")
        assert strat.drop_cap == Decimal("0.09")
        assert strat.horizon_seconds == 2400

    def test_threshold_honored(self):
        """Tighter threshold requires bigger drop before signaling."""
        tight = BistIntradayReversion(drop_threshold=Decimal("0.06"))
        loose = BistIntradayReversion(drop_threshold=Decimal("0.01"))
        assert tight.drop_threshold > loose.drop_threshold


# ---------------------------------------------------------------------------
# BistVolumeBreakout
# ---------------------------------------------------------------------------

class TestBistVolumeBreakoutParams:
    def test_defaults(self):
        strat = BistVolumeBreakout()
        assert strat.vol_mult == VOL_MULT
        assert strat.vol_mult_cap == VOL_MULT_CAP
        assert strat.horizon_seconds == VB_HORIZON_S

    def test_custom_vol_mult(self):
        strat = BistVolumeBreakout(vol_mult=Decimal("5.0"))
        assert strat.vol_mult == Decimal("5.0")
        assert strat.vol_mult_cap == VOL_MULT_CAP

    def test_custom_vol_mult_cap(self):
        strat = BistVolumeBreakout(vol_mult_cap=Decimal("12.0"))
        assert strat.vol_mult_cap == Decimal("12.0")

    def test_custom_horizon_s(self):
        strat = BistVolumeBreakout(horizon_s=450)
        assert strat.horizon_seconds == 450

    def test_all_custom(self):
        strat = BistVolumeBreakout(
            vol_mult=Decimal("4.0"),
            vol_mult_cap=Decimal("10.0"),
            horizon_s=600,
        )
        assert strat.vol_mult == Decimal("4.0")
        assert strat.vol_mult_cap == Decimal("10.0")
        assert strat.horizon_seconds == 600

    def test_threshold_honored(self):
        """Higher vol_mult means generate() only fires on larger volume spikes."""
        strict = BistVolumeBreakout(vol_mult=Decimal("7.0"))
        lax = BistVolumeBreakout(vol_mult=Decimal("2.0"))
        assert strict.vol_mult > lax.vol_mult
