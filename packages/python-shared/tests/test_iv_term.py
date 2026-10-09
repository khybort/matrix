"""matrix_shared.iv_term: the D.3 rule shared by the recorder, the shadow module
and the r5f builder. Reproduced round 5's TERM, pct and 118 D.3 entries exactly
on its cached data (2026-10-09); these tests pin the pieces."""

from __future__ import annotations

from datetime import UTC, datetime

from matrix_shared import iv_term as T

D = int(datetime(2026, 10, 9, tzinfo=UTC).timestamp() * 1000)


def _trade(name, iv, idx=100_000.0, ts=D - 3_600_000, amount=1.0):
    return {"timestamp": ts, "instrument_name": name, "iv": iv, "index_price": idx, "amount": amount}


def test_parse_instrument():
    exp, k, typ = T.parse_instrument("BTC-16OCT26-100000-C")
    assert datetime.fromtimestamp(exp / 1000, UTC) == datetime(2026, 10, 16, 8, tzinfo=UTC)
    assert k == 100_000.0 and typ == "C"


def test_term_is_front_minus_back_atm_median():
    front = [_trade("BTC-16OCT26-100000-C", 60 + i) for i in range(5)]  # ~7 DTE, ATM
    back = [_trade("BTC-25DEC26-100000-P", 50.0) for _ in range(5)]  # ~77 DTE, ATM
    wing = [_trade("BTC-16OCT26-150000-C", 200.0) for _ in range(5)]  # far OTM: not ATM
    late = [_trade("BTC-16OCT26-100000-C", 999.0, ts=D)]  # at D: outside [D-4h, D)
    f = T.term_features(front + back + wing + late, D)
    assert f["n_trades"] == 15
    assert f["n_front"] == 5 and f["n_back"] == 5
    assert f["term"] == 62.0 - 50.0
    assert f["put_notional"] == 5 * 100_000.0


def test_thin_bucket_means_missing():
    front = [_trade("BTC-16OCT26-100000-C", 60.0) for _ in range(4)]
    back = [_trade("BTC-25DEC26-100000-P", 50.0) for _ in range(5)]
    assert T.term_features(front + back, D)["term"] is None


def _history(values):
    start = D - len(values) * T.DAY_MS
    return {start + i * T.DAY_MS: v for i, v in enumerate(values)}


def test_percentile_needs_120_values_in_365_days():
    h = _history([float(i % 10) for i in range(119)] + [5.0])
    assert T.percentile(h, max(h)) is None
    h = _history([float(i % 10) for i in range(120)] + [5.0])
    assert T.percentile(h, max(h)) == 0.5


def test_one_episode_per_window_and_skip_while_open():
    base = [0.0] * 200
    # day 200: window starts (fires 2 days) -> one entry; day 202 off; day 203 fires again
    # while the 72 h episode from day 200 is open (exit day 203 01:00) -> skipped;
    # day 210 a fresh window -> entry.
    vals = base + [100.0, 100.0, 0.0, 100.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 100.0]
    dec = T.decisions(_history(vals))
    entries = [d for d in dec if d["enter"]]
    assert [d["day"] for d in entries] == [min(_history(vals)) + k * T.DAY_MS for k in (200, 210)]
    e = entries[0]
    assert e["entry_ms"] == e["day"] + 3_600_000 and e["exit_ms"] == e["entry_ms"] + 72 * 3_600_000


def test_start_does_not_change_verdicts():
    vals = [0.0] * 200 + [100.0, 100.0, 0.0, 100.0]
    h = _history(vals)
    full = {d["day"]: d["enter"] for d in T.decisions(h)}
    tail = T.decisions(h, start_ms=max(h) - T.DAY_MS)
    assert all(full[d["day"]] == d["enter"] for d in tail) and len(tail) == 2
