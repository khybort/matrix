"""Small-sample statistics shared by the learning loop (docs/AUTONOMY_PLAN.md P1.5)."""

from __future__ import annotations

import math


def wilson_bounds(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """(lower, upper) Wilson score interval for a binomial proportion."""
    if n <= 0:
        return 0.0, 1.0
    p = wins / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return max(0.0, (centre - margin) / denom), min(1.0, (centre + margin) / denom)


def wilson_lower(wins: int, n: int, z: float = 1.96) -> float:
    return wilson_bounds(wins, n, z)[0]


def wilson_upper(wins: int, n: int, z: float = 1.96) -> float:
    return wilson_bounds(wins, n, z)[1]
