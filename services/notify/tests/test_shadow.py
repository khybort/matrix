"""Shadow tracker: decomposition, verdicts and the alert dedupe. Synthetic rows only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from matrix_shared import shadow_tracker as st

from notify.health import ALERT_INFO, ALERT_WARNING
from notify.shadow import detect_shadow_alerts, load_state, save_state

T0 = datetime(2026, 10, 10, 1, 0, tzinfo=UTC)
BAND = {**st.DEFAULT_BANDS[("neg_funding_carry", "crypto")], "since": "2026-10-09T13:30:00+00:00"}


def row(sym: str, opened: datetime, *, net_bps: float | None = 150.0, notional: float = 400.0,
        hold_h: float = 48.0, book_bps: float | None = 50.0, borrow_usd: str | None = "1.2",
        borrow_rate: str | None = "0.00002", generated: datetime | None = None) -> dict:
    """One filled carry. `net_bps` None = still open."""
    closed = net_bps is not None
    return {
        "strategy_id": "neg_funding_carry", "asset_class": "crypto", "symbol": sym, "side": "inverse_carry",
        "generated_at": generated or opened, "horizon_seconds": 172800,
        "notional_usd": notional, "opened_at": opened,
        "closed_at": opened + timedelta(hours=hold_h) if closed else None,
        "pnl_usd": notional * net_bps / 10_000 if closed else None,
        "borrow_rate_hourly": borrow_rate, "borrow_charged_usd": borrow_usd if closed else None,
        "book_close_bps": book_bps if closed else None,
    }


def closed_set(n: int, net: float, days: int = 5) -> list[dict]:
    start = T0 - timedelta(days=12)
    return [row(f"C{i}USDT", start + timedelta(days=i % days, minutes=i), net_bps=net + (i % 3 - 1) * 20)
            for i in range(n)]


def evaluate(rows, *, now=T0 + timedelta(days=1), qualifying=0, band=BAND):
    return st.evaluate(rows, band, now=now, qualifying=qualifying,
                       strategy_id="neg_funding_carry", asset_class="crypto")


# ------------------------------------------------------------- decomposition

def test_decompose_splits_funding_borrow_book():
    (ep,) = st.decompose([row("KAIAUSDT", T0, net_bps=100.0, notional=400.0, book_bps=50.0, borrow_usd="2.0")])
    assert ep.closed and ep.anomalies == []
    assert ep.net_bps == pytest.approx(100.0)
    assert ep.bps(ep.book_usd) == pytest.approx(50.0)
    assert ep.bps(ep.borrow_usd) == pytest.approx(50.0)  # $2 on $400
    assert ep.bps(ep.funding_usd) == pytest.approx(200.0)  # net + book + borrow


def test_reemissions_are_one_episode():
    a = row("KAIAUSDT", T0, net_bps=100.0, notional=200.0)
    b = row("KAIAUSDT", T0 + timedelta(hours=1), net_bps=300.0, notional=200.0)
    eps = st.decompose([b, a])
    assert len(eps) == 1
    assert eps[0].net_bps == pytest.approx(200.0)  # dollars summed over the episode's notional
    rep = evaluate([a, b])
    assert rep["opened"] == 1 and rep["closed"] == 1 and rep["n_raw"] == 2


def test_zero_funding_on_a_long_hold_is_an_anomaly():
    # net = -(book + borrow) exactly: funding booked 0 over 48 h.
    r = row("KAIAUSDT", T0, net_bps=-80.0, notional=400.0, book_bps=50.0, borrow_usd="1.2")
    (ep,) = st.decompose([r])
    assert ep.anomalies == ["zero_funding"]
    short = row("KAIAUSDT", T0, net_bps=-80.0, notional=400.0, book_bps=50.0, borrow_usd="1.2", hold_h=2)
    assert st.decompose([short])[0].anomalies == []  # crossed no settlement: zero is right


@pytest.mark.parametrize("kw", [{"borrow_usd": None}, {"borrow_usd": "0"}, {"borrow_rate": None}])
def test_borrow_not_charged(kw):
    (ep,) = st.decompose([row("KAIAUSDT", T0, **kw)])
    assert "borrow_not_charged" in ep.anomalies


def test_flat_cost_close_is_book_not_charged_and_undecomposable():
    (ep,) = st.decompose([row("KAIAUSDT", T0, book_bps=None)])
    assert ep.anomalies == ["book_not_charged"] and ep.funding_usd is None


def test_components_limit_the_checks():
    (ep,) = st.decompose([row("KAIAUSDT", T0, book_bps=None, borrow_usd=None)], components=["funding"])
    assert ep.anomalies == []


def test_clustered_t():
    assert st.clustered_t([1.0], ["a"]) is None
    assert st.clustered_t([1.0, 2.0], ["a", "a"]) is None  # one cluster
    # Same values: clustering by day widens the error when a day's episodes move together.
    vals = [10.0, 12.0, 11.0, -1.0, 0.0, -2.0]
    iid = st.clustered_t(vals, list(range(6)))
    by_day = st.clustered_t(vals, ["d1"] * 3 + ["d2"] * 3)
    assert iid is not None and by_day is not None and by_day < iid


# ------------------------------------------------------------------ verdicts

def test_collecting_below_min_episodes():
    rep = evaluate(closed_set(19, 200.0) + [row("OPENUSDT", T0, net_bps=None)])
    assert rep["verdict"] == st.COLLECTING
    assert rep["closed"] == 19 and rep["open_now"] == 1 and rep["opened"] == 20


def test_on_track_and_below_band():
    on = evaluate(closed_set(25, 150.0))
    assert on["verdict"] == st.ON_TRACK
    assert on["mean_bps"] == pytest.approx(150.0, abs=5)
    assert on["t_day"] is not None and on["n_days"] == 5
    assert on["funding_bps"] > on["mean_bps"]  # costs are positive and subtracted
    below = evaluate(closed_set(25, 30.0))  # mean 29.2: at the floor, costs ate it
    assert below["mean_bps"] == pytest.approx(29.2) and below["verdict"] == st.BELOW_BAND


def test_stale_only_when_the_watchlist_had_opportunities():
    rows = closed_set(25, 150.0)
    late = T0 + timedelta(days=5)  # last open ≥ 72 h before
    assert evaluate(rows, now=late, qualifying=12)["verdict"] == st.BROKEN
    assert evaluate(rows, now=late, qualifying=12)["reasons"] == ["stale"]
    assert evaluate(rows, now=late, qualifying=3)["verdict"] == st.ON_TRACK
    assert evaluate(rows, now=late, qualifying=None)["verdict"] == st.ON_TRACK  # unknown ≠ broken


def test_stale_with_no_fill_since_registration():
    since = datetime.fromisoformat(BAND["since"])
    assert evaluate([], now=since + timedelta(hours=73), qualifying=40)["verdict"] == st.BROKEN
    assert evaluate([], now=since + timedelta(hours=71), qualifying=40)["verdict"] == st.COLLECTING


def test_anomaly_breaks_even_while_collecting():
    rep = evaluate([row("KAIAUSDT", T0, borrow_usd=None)])
    assert rep["verdict"] == st.BROKEN and rep["reasons"] == ["borrow_not_charged"]
    assert "borrow not charged: 1 ep (KAIAUSDT)" in st.format_line(rep)


def test_band_thresholds_come_from_the_band():
    band = {**BAND, "min_episodes": 5, "floor_bps": 200.0}
    assert evaluate(closed_set(6, 150.0), band=band)["verdict"] == st.BELOW_BAND


def test_format_line():
    line = st.format_line(evaluate(closed_set(25, 150.0), qualifying=7))
    assert line.startswith("shadow neg_funding_carry/crypto: ON_TRACK — 25/20 closed ep, 0 open")
    assert "band +100…+300, floor +30" in line and "7 qualifying settlements/72h" in line
    assert "funding +" in line and "t_day" in line


# -------------------------------------------------------------- notify dedupe

def test_alert_on_change_and_rate_limited_broken():
    now = T0
    collecting = evaluate([row("A", T0, net_bps=None)])
    alerts, state = detect_shadow_alerts({}, [collecting], now)
    assert alerts == []  # a quiet first sighting
    alerts, state = detect_shadow_alerts(state, [collecting], now)
    assert alerts == []

    broken = evaluate([row("A", T0, borrow_usd=None)])
    alerts, state = detect_shadow_alerts(state, [broken], now)
    assert len(alerts) == 1 and alerts[0][0] == ALERT_WARNING
    assert "collecting → broken" in alerts[0][1]
    alerts, state = detect_shadow_alerts(state, [broken], now + timedelta(hours=2))
    assert alerts == []  # deduped
    alerts, state = detect_shadow_alerts(state, [broken], now + timedelta(hours=25))
    assert len(alerts) == 1  # persisting broken re-alerts daily

    on = evaluate(closed_set(25, 150.0))
    alerts, state = detect_shadow_alerts(state, [on], now + timedelta(hours=26))
    assert alerts[0][0] == ALERT_INFO and "broken → on_track" in alerts[0][1]


def test_first_sighting_of_a_loud_verdict_alerts():
    alerts, _ = detect_shadow_alerts({}, [evaluate(closed_set(25, 10.0))], T0)
    assert len(alerts) == 1 and "below_band" in alerts[0][1] and "do not promote" in alerts[0][1]


def test_new_broken_reason_is_news():
    a = evaluate([row("A", T0, borrow_usd=None)])
    b = evaluate([row("A", T0, borrow_usd=None, book_bps=None)])
    _, state = detect_shadow_alerts({}, [a], T0)
    alerts, _ = detect_shadow_alerts(state, [b], T0 + timedelta(minutes=15))
    assert len(alerts) == 1


def test_state_roundtrip(tmp_path):
    p = tmp_path / "s.json"
    _, state = detect_shadow_alerts({}, [evaluate(closed_set(25, 10.0))], T0)
    save_state(state, p)
    assert load_state(p) == state
    assert load_state(tmp_path / "missing.json") == {}
