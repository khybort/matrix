"""BIST-market strategy modules.

Add the class to `STRATEGIES` to register it with the dispatcher.
"""

from __future__ import annotations

from strategy.modules.bist.gap_fade import BistGapFade
from strategy.modules.bist.intraday_reversion import BistIntradayReversion
from strategy.modules.bist.news_event import BistNewsEvent
from strategy.modules.bist.volume_breakout import BistVolumeBreakout

STRATEGIES: list[type] = [
    BistGapFade,
    BistIntradayReversion,
    BistVolumeBreakout,
    BistNewsEvent,
]

__all__ = [
    "BistGapFade",
    "BistIntradayReversion",
    "BistNewsEvent",
    "BistVolumeBreakout",
    "STRATEGIES",
]
