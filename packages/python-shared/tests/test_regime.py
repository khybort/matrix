"""matrix_shared.regime — pure classification + filter matching."""

from __future__ import annotations

import math

from matrix_shared.regime import UNKNOWN_REGIME, classify, regime_matches


def _series(hours: int, drift: float = 0.0, noise: float = 0.002, seed: int = 1) -> list[float]:
    import random
    rnd = random.Random(seed)
    px, out = 100.0, []
    for _ in range(hours):
        px *= math.exp(drift + rnd.gauss(0, noise))
        out.append(px)
    return out


def test_too_little_data_is_unknown():
    assert classify(_series(10), 0.0) is UNKNOWN_REGIME


def test_trend_and_funding_axes():
    up = classify(_series(24 * 7, drift=0.002), 0.0005)
    assert up.trend == "up" and up.funding == "pos"
    down = classify(_series(24 * 7, drift=-0.002), -0.0005)
    assert down.trend == "down" and down.funding == "neg"
    flat = classify(_series(24 * 7, drift=0.0, noise=0.0005), None)
    assert flat.trend == "flat" and flat.funding == "unknown"


def test_vol_axis_compares_last_day_to_week_median():
    calm = _series(24 * 6, noise=0.001)
    last = calm[-1]
    import random
    rnd = random.Random(7)
    wild = [last := last * math.exp(rnd.gauss(0, 0.01)) for _ in range(24)]
    r = classify(calm + wild, 0.0)
    assert r.vol == "high" and r.vol_ratio and r.vol_ratio > 1.4
    assert classify(_series(24 * 7, noise=0.001), 0.0).vol == "mid"


def test_key_and_wildcard_matching():
    r = classify(_series(24 * 7, drift=-0.002), 0.0005)
    assert r.key == f"{r.vol}/down/pos"
    assert regime_matches("*/down/*", r.key) and regime_matches(r.key, r.key)
    assert not regime_matches("*/up/*", r.key)
    assert not regime_matches("high/down/pos", None)
