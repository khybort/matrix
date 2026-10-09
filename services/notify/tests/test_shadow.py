"""Shadow tracker: decomposition, verdicts and the alert dedupe. Synthetic rows only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from matrix_shared import shadow_tracker as st

from notify.health import ALERT_INFO, ALERT_WARNING
from notify import shadow as ns
from notify.shadow import deliver_shadow_alerts, detect_shadow_alerts, load_state, save_state

T0 = datetime(2026, 10, 10, 1, 0, tzinfo=UTC)
BAND = {**st.DEFAULT_BANDS[("neg_funding_carry", "crypto")], "since": "2026-10-09T13:30:00+00:00"}


def row(sym: str, opened: datetime, *, net_bps: float | None = 150.0, notional: float = 400.0,
        hold_h: float = 48.0, book_bps: float | None = 50.0, borrow_usd: str | None = "1.2",
        borrow_rate: str | None = "0.00002", generated: datetime | None = None,
        source: str | None = None, series_mean: str | None = None,
        flat_keep: str | None = None, naive_keep: str | None = None, decay_keep: str | None = None) -> dict:
    """One filled carry. `net_bps` None = still open. Keeps are json text ("true"/"false")."""
    closed = net_bps is not None
    return {
        "borrow_source": source if closed else None, "borrow_series_mean_hourly": series_mean if closed else None,
        "flat_keep": flat_keep, "naive_keep": naive_keep, "decay_keep": decay_keep,
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


def sent(state, reports, now, **kw):
    """detect_shadow_alerts on a channel that delivers everything."""
    alerts, state = detect_shadow_alerts(state, reports, now, **kw)
    for a in alerts:
        ns.mark_delivered(state, a)
    return alerts, state


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


@pytest.mark.parametrize("kw", [
    {"borrow_usd": None}, {"borrow_usd": "0"}, {"borrow_rate": None},
    {"borrow_usd": None, "borrow_rate": "0"},  # zero quote, but the close never ran the borrow path
    {"borrow_usd": "0", "source": "stressed_entry"},  # positive quote, fallback x stress not applied
    {"borrow_usd": "0", "source": "mixed", "series_mean": "0"},  # gap hours owed the fallback
    {"borrow_usd": "0", "source": "series", "series_mean": "0.00001"},
])
def test_borrow_not_charged(kw):
    (ep,) = st.decompose([row("KAIAUSDT", T0, **kw)])
    assert "borrow_not_charged" in ep.anomalies


@pytest.mark.parametrize("kw", [
    {"borrow_usd": "0", "borrow_rate": "0"},  # the coin borrowed free at entry: quote x stress = 0
    {"borrow_usd": "0", "borrow_rate": "0E-8", "source": "stressed_entry"},
    {"borrow_usd": "0", "source": "series", "series_mean": "0"},  # every hour recorded at zero
    {"borrow_usd": "0.0", "source": "series", "series_mean": "0E-10"},
])
def test_genuinely_zero_borrow_is_not_broken(kw):
    """A zero charge on a zero recorded quote (entry quote or the whole hourly
    series) is the venue's price, not a missed charge: it must not fire `broken`."""
    (ep,) = st.decompose([row("KAIAUSDT", T0, **kw)])
    assert ep.anomalies == []
    assert ep.borrow_usd == 0.0 and ep.bps(ep.funding_usd) == pytest.approx(150.0 + 50.0)
    rep = evaluate([row("KAIAUSDT", T0, **kw)])
    assert rep["verdict"] != st.BROKEN and rep["reasons"] == []


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
    alerts, state = sent({}, [collecting], now)
    assert alerts == []  # a quiet first sighting
    alerts, state = sent(state, [collecting], now)
    assert alerts == []

    broken = evaluate([row("A", T0, borrow_usd=None)])
    alerts, state = sent(state, [broken], now)
    assert len(alerts) == 1 and alerts[0][0] == ALERT_WARNING
    assert "collecting → broken" in alerts[0][1]
    alerts, state = sent(state, [broken], now + timedelta(hours=2))
    assert alerts == []  # deduped
    alerts, state = sent(state, [broken], now + timedelta(hours=25))
    assert len(alerts) == 1  # persisting broken re-alerts daily

    on = evaluate(closed_set(25, 150.0))
    alerts, state = sent(state, [on], now + timedelta(hours=26))
    assert alerts[0][0] == ALERT_INFO and "broken → on_track" in alerts[0][1]


def test_first_sighting_of_a_loud_verdict_alerts():
    alerts, _ = sent({}, [evaluate(closed_set(25, 10.0))], T0)
    assert len(alerts) == 1 and "below_band" in alerts[0][1] and "do not promote" in alerts[0][1]


def test_new_broken_reason_is_news():
    a = evaluate([row("A", T0, borrow_usd=None)])
    b = evaluate([row("A", T0, borrow_usd=None, book_bps=None)])
    _, state = sent({}, [a], T0)
    alerts, _ = sent(state, [b], T0 + timedelta(minutes=15))
    assert len(alerts) == 1


def test_undelivered_alert_is_not_marked_sent():
    """Telegram unreachable: the verdict change and the once-only review_due
    stay pending and fire again on the next tick; once delivered, never again."""
    rows = (series_set(20, 120.0, ratio=0.9)
            + series_set(10, -40.0, ratio=1.4, flat_keep="false", decay_keep="false", prefix="F"))
    broken = evaluate(rows + [row("A", T0, borrow_usd=None)])
    assert broken["verdict"] == st.BROKEN and broken["revisit"]["due"]
    _, state = sent({}, [evaluate([row("A", T0, net_bps=None)])], T0)  # quiet: collecting

    async def down(level, text):
        return False

    async def up(level, text):
        return True

    import asyncio

    for k in range(3):  # three ticks with Telegram down
        alerts, state = detect_shadow_alerts(state, [broken], T0 + timedelta(minutes=15 * (k + 1)))
        assert len(alerts) == 2 and any("review_due" in a.text for a in alerts)
        assert asyncio.run(deliver_shadow_alerts(state, alerts, down)) == 0
        key = "neg_funding_carry/crypto"
        assert state[key]["verdict"] == "collecting" and not state[key]["review_sent"]

    alerts, state = detect_shadow_alerts(state, [broken], T0 + timedelta(hours=1))
    assert asyncio.run(deliver_shadow_alerts(state, alerts, up)) == 2
    assert state[key]["verdict"] == st.BROKEN and state[key]["review_sent"]
    alerts, state = detect_shadow_alerts(state, [broken], T0 + timedelta(hours=2))
    assert alerts == []

    # One of two delivered: only that one is marked.
    fresh = {}
    alerts, fresh = detect_shadow_alerts(fresh, [broken], T0)
    calls = iter([True, False])

    async def flaky(level, text):
        return next(calls)

    asyncio.run(deliver_shadow_alerts(fresh, alerts, flaky))
    assert fresh[key]["verdict"] == st.BROKEN and not fresh[key]["review_sent"]
    alerts, _ = detect_shadow_alerts(fresh, [broken], T0 + timedelta(minutes=15))
    assert [("review_due" in a.text) for a in alerts] == [True]


@pytest.mark.asyncio
async def test_state_survives_in_the_db():
    """The state is in notify_alert_state (local DB), not /tmp: a container
    recreate keeps `review_sent` and the last verdict."""
    import uuid

    from matrix_shared import local_session_scope
    from sqlalchemy import text

    key = f"test_shadow_{uuid.uuid4().hex}"
    _, state = sent({}, [evaluate(closed_set(25, 10.0))], T0)
    try:
        assert await load_state(key) == {}
        assert await save_state(state, key)
        assert await load_state(key) == state
        state["x/y"] = {"sig": "broken", "verdict": "broken", "alerted_at": 1.0, "review_sent": True}
        assert await save_state(state, key)
        assert (await load_state(key))["x/y"]["review_sent"] is True
    finally:
        async with local_session_scope() as s:
            await s.execute(text("DELETE FROM notify_alert_state WHERE key = :k"), {"k": key})


@pytest.mark.asyncio
async def test_unreadable_state_is_none_not_empty(monkeypatch):
    """A DB error must not read as "nothing sent yet": that would re-fire
    every loud verdict and every review_due."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def down():
        raise OSError("db down")
        yield

    monkeypatch.setattr(ns, "local_session_scope", down)
    assert await load_state("whatever") is None
    assert await save_state({}, "whatever") is False


# --------------------------------------------- borrow source / entry-rule arms

def series_set(n: int, net: float, *, ratio: float = 0.8, flat_keep: str = "true", decay_keep: str = "true",
               source: str = "series", days: int = 5, prefix: str = "S") -> list[dict]:
    start = T0 - timedelta(days=12)
    return [row(f"{prefix}{i}USDT", start + timedelta(days=i % days, minutes=i), net_bps=net + (i % 3 - 1) * 20,
                source=source, series_mean=str(0.00002 * ratio), flat_keep=flat_keep, naive_keep="true",
                decay_keep=decay_keep)
            for i in range(n)]


def test_splits_by_borrow_source_and_entry_arms():
    rows = (series_set(6, 100.0, flat_keep="true", decay_keep="true")
            + series_set(4, -50.0, flat_keep="false", decay_keep="false", prefix="F")
            + series_set(3, 200.0, source="stressed_entry", prefix="X")
            + [row("OLDUSDT", T0 - timedelta(days=2))])  # closed before the recorder: no source, no keeps
    rep = evaluate(rows)
    src = rep["by_borrow_source"]
    assert src["series"]["n"] == 10 and src["stressed_entry"]["n"] == 3 and src["unknown"]["n"] == 1
    assert src["series"]["mean_bps"] == pytest.approx((600.0 - 220.0) / 10)
    flat = rep["arms"]["flat_keep"]
    assert flat["true"]["n"] == 9 and flat["false"]["n"] == 4  # stressed_entry counted in the display arms
    assert flat["false"]["mean_bps"] == pytest.approx(-50.0, abs=10)
    assert flat["false"]["median_bps"] is not None and flat["false"]["t_day"] is not None
    assert rep["arms"]["decay_keep"]["false"]["n"] == 4
    line = st.format_line(rep)
    assert "borrow src 10/0/3 series/mixed/stressed (+1 unknown), review at 10/30 series" in line


def test_reemissions_with_different_sources_are_mixed():
    a = row("KAIAUSDT", T0, source="series", series_mean="0.00002")
    b = row("KAIAUSDT", T0 + timedelta(hours=1), source="stressed_entry")
    (ep,) = st.decompose([a, b])
    assert ep.borrow_source == "mixed"
    assert ep.borrow_ratio == pytest.approx(1.0)  # only the row with a series mean


def test_revisit_not_due_below_threshold():
    rv = evaluate(series_set(29, 100.0))["revisit"]
    assert rv["series_n"] == 29 and rv["due"] is False


def test_stressed_and_mixed_episodes_are_excluded_from_the_borrow_verdict():
    # 30 series episodes that win, plus losing flat_keep=false episodes and
    # high-ratio ones charged at an assumed borrow: those must not move the rules.
    rows = (series_set(30, 100.0, ratio=0.7)
            + series_set(20, -300.0, ratio=3.0, flat_keep="false", decay_keep="false",
                         source="stressed_entry", prefix="X")
            + series_set(5, -300.0, ratio=3.0, flat_keep="false", decay_keep="false", source="mixed", prefix="M"))
    rv = evaluate(rows)["revisit"]
    assert rv["due"] and rv["series_n"] == 30
    rules = {r["rule"]: r for r in rv["rules"]}
    assert rules["hold_stress"]["holds"] is False and rules["hold_stress"]["p90"] == pytest.approx(0.7)
    assert rules["borrow_model"]["n"] == 0 and rules["borrow_model"]["holds"] is False
    assert rules["expected_model"]["n"] == 0 and rules["expected_model"]["holds"] is False


def test_revisit_rules_hold_with_the_implied_env():
    rows = (series_set(20, 120.0, ratio=0.9)
            + series_set(10, -40.0, ratio=1.4, flat_keep="false", decay_keep="false", prefix="F"))
    rv = evaluate(rows)["revisit"]
    rules = {r["rule"]: r for r in rv["rules"]}
    assert rules["hold_stress"]["holds"] and rules["hold_stress"]["value"] == "1.40"
    assert rules["borrow_model"]["holds"] and rules["borrow_model"]["env"] == "MATRIX_NFC_BORROW_MODEL"
    assert rules["expected_model"]["holds"] and rules["expected_model"]["value"] == "decay"


def test_review_due_fires_exactly_once():
    rows = (series_set(20, 120.0, ratio=0.9)
            + series_set(10, -40.0, ratio=1.4, flat_keep="false", decay_keep="true", prefix="F"))
    early = evaluate(rows[:29])
    alerts, state = sent({}, [early], T0)
    assert not any("review_due" in a[1] for a in alerts)

    rep = evaluate(rows)
    alerts, state = sent(state, [rep], T0 + timedelta(minutes=15))
    reviews = [a for a in alerts if "review_due" in a[1]]
    assert len(reviews) == 1 and reviews[0][0] == ALERT_WARNING
    text = reviews[0][1]
    assert text.startswith("🔁 review_due neg_funding_carry/crypto: 30 closed episodes with borrow_source=series")
    assert "(hold_stress) p90 > 1.0: HOLDS" in text
    assert "(borrow_model) mean ≤ 0: HOLDS" in text
    assert "(expected_model) mean ≤ 0: does not hold" in text
    assert "MATRIX_NFC_BORROW_HOLD_STRESS=1.40 MATRIX_NFC_BORROW_MODEL=flat" in text

    for k in range(1, 4):  # later ticks, more episodes, a restart from the saved state: never again
        more = evaluate(rows + series_set(k, 50.0, prefix=f"N{k}_"))
        alerts, state = sent(state, [more], T0 + timedelta(hours=k))
        assert not any("review_due" in a[1] for a in alerts)


def test_review_due_with_no_rule_holding_says_keep():
    rep = evaluate(series_set(30, 100.0))
    alerts, _ = sent({}, [rep], T0)
    (text,) = [a[1] for a in alerts if "review_due" in a[1]]
    assert "no revisit rule holds: keep the current env." in text


def test_band_without_revisit_has_none():
    band = {k: v for k, v in BAND.items() if k != "revisit_series_episodes"}
    rep = evaluate(series_set(40, 100.0), band=band)
    assert rep["revisit"] is None
    assert "review at" not in st.format_line(rep)
    alerts, _ = detect_shadow_alerts({}, [rep], T0)
    assert alerts == []


# -------------------------------------------- executable episodes only (2026-10-09)

def _would_abort(r: dict) -> dict:
    return {**r, "exec_precheck": "would_abort"}


def test_would_abort_episodes_are_excluded_from_verdict_and_band():
    """A position the live executor would have aborted at open is not evidence:
    25 good executable episodes stay on track although 10 would-abort losers
    ride along, and those 10 alone never make a verdict."""
    good = closed_set(25, 150.0)
    bad = [_would_abort(row(f"X{i}USDT", T0 - timedelta(days=3, minutes=i), net_bps=-400.0)) for i in range(10)]
    rep = evaluate(good + bad)
    assert rep["verdict"] == st.ON_TRACK and rep["closed"] == 25
    assert rep["mean_bps"] == pytest.approx(evaluate(good)["mean_bps"])
    assert rep["would_abort"] == 10 and rep["would_abort_closed"] == 10
    only = evaluate(bad)
    assert only["closed"] == 0 and only["verdict"] == st.COLLECTING and only["would_abort"] == 10


def test_would_abort_reemissions_count_once_and_tagging_is_per_position():
    a = _would_abort(row("KAIAUSDT", T0, net_bps=-50.0, notional=200.0))
    b = _would_abort(row("KAIAUSDT", T0 + timedelta(hours=1), net_bps=-50.0, notional=200.0))
    ok = row("SKLUSDT", T0, net_bps=120.0)
    rep = evaluate([a, b, ok])
    assert rep["would_abort"] == 1 and rep["closed"] == 1 and rep["mean_bps"] == pytest.approx(120.0)


def test_digest_line_reports_would_abort_and_executor_refused():
    rows = closed_set(3, 150.0) + [_would_abort(row("KAIAUSDT", T0, net_bps=None))]
    rep = st.evaluate(rows, BAND, now=T0 + timedelta(days=1), qualifying=4,
                      refused={"borrow_drift": 2, "books": 1},
                      strategy_id="neg_funding_carry", asset_class="crypto")
    assert rep["open_now"] == 0 and rep["refused"] == 3
    line = st.format_line(rep)
    assert "executable only: 1 would-abort ep excluded, 3 executor refused (books 1, borrow_drift 2)" in line
    clean = st.format_line(evaluate(closed_set(3, 150.0)))
    assert "executable only: 0 would-abort ep excluded, 0 executor refused" in clean
