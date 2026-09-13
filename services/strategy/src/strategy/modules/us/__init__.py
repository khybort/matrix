"""US-market strategy modules.

Add the class to `STRATEGIES` to register it with the dispatcher.
"""

from __future__ import annotations

from strategy.modules.us.gap_fade import UsGapFade
from strategy.modules.us.intraday_reversion import UsIntradayReversion
from strategy.modules.us.momentum import UsMomentum
from strategy.modules.us.news_event import UsNewsEvent
from strategy.modules.us.volume_breakout import UsVolumeBreakout

STRATEGIES: list[type] = [
    UsGapFade,
    UsIntradayReversion,
    UsVolumeBreakout,
    UsMomentum,
    UsNewsEvent,
]

__all__ = [
    "UsGapFade",
    "UsIntradayReversion",
    "UsMomentum",
    "UsNewsEvent",
    "UsVolumeBreakout",
    "STRATEGIES",
]
