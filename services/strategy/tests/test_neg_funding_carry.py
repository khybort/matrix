"""neg_funding_carry: enter on the last *settled* negative rate, only for coins
with a published borrow rate, carrying that rate for the book to charge."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete

from matrix_shared import local_session_scope
from matrix_shared.models import TickerSnapshot

from strategy.modules.crypto import neg_funding_carry as M

pytestmark = pytest.mark.asyncio

SYM = "NFCTESTUSDT"  # base coin NFCTEST
_REAL_PRIOR = M.prior_settlements  # before the autouse stub replaces it


async def _ticker(ts: datetime, rate: str, nxt: datetime) -> None:
    async with local_session_scope() as s:
        s.add(TickerSnapshot(id=uuid.uuid4(), exchange="bybit", symbol=SYM, snapshot_ts=ts,
                             last_price=Decimal("2"), mark_price=Decimal("2"),
                             funding_rate=Decimal(rate), next_funding_ts=nxt))


@pytest.fixture
async def clean():
    async with local_session_scope() as s:
        await s.execute(delete(TickerSnapshot).where(TickerSnapshot.symbol == SYM))
    yield
    async with local_session_scope() as s:
        await s.execute(delete(TickerSnapshot).where(TickerSnapshot.symbol == SYM))


@pytest.fixture(autouse=True)
def _no_history(monkeypatch):
    """No network in tests: the prior-settlement history is unknown unless a
    test supplies it."""
    async def none(symbol, settled_at):
        return None
    monkeypatch.setattr(M, "prior_settlements", none)


def _borrow(monkeypatch, table):
    async def fake():
        return table
    monkeypatch.setattr(M, "borrow_rates", fake)


def _flat_book(mid: float, half_spread_bps: float, usd_per_level: float, n: int = 20,
               step_bps: float = 2.0) -> M.Book:
    """Symmetric ladder: best quotes `half_spread_bps` off mid, then `step_bps`
    apart, `usd_per_level` notional at each level."""
    bids, asks = [], []
    for i in range(n):
        off = (half_spread_bps + i * step_bps) / 1e4
        bp, ap = mid * (1 - off), mid * (1 + off)
        bids.append((bp, usd_per_level / bp))
        asks.append((ap, usd_per_level / ap))
    return M.Book(bids, asks, "test")


def _books(monkeypatch, perp: M.Book | None, spot: M.Book | None, seen: list | None = None):
    async def fake(venue, category, symbol, session=None):
        if seen is not None:
            seen.append((venue, category, symbol))
        return perp if category == "linear" else spot
    monkeypatch.setattr(M, "fetch_book", fake)


DEEP = _flat_book(2.0, 1.0, 5_000)  # ~1 bp off mid, $5k per level


async def _settled(rate: str, minutes_ago: int = 20) -> None:
    now = datetime.now(UTC)
    s = now - timedelta(minutes=minutes_ago)
    await _ticker(s - timedelta(minutes=2), rate, s)  # rate in force at settlement S
    await _ticker(now, "0.0000125", s + timedelta(hours=8))  # post-settlement placeholder


def test_base_coin_strips_multiplier_prefix():
    assert M.base_coin("1000PEPEUSDT") == "PEPE"
    assert M.base_coin("BTCUSDT") == "BTC"
    assert M.base_coin("1INCHUSDT") == "1INCH"


def test_parse_borrow_takes_cheapest_lending_venue():
    bybit = {"result": {"vipCoinList": [{"list": [
        {"currency": "AAA", "borrowable": True, "hourlyBorrowRate": "0.00001"},
        {"currency": "BBB", "borrowable": False, "hourlyBorrowRate": ""},
    ]}]}}
    binance = {"data": [
        {"assetName": "AAA", "specs": [{"vipLevel": "0", "dailyInterestRate": "0.00048"}]},  # 0.00002/h
        {"assetName": "CCC", "specs": [{"vipLevel": "0", "dailyInterestRate": "0.00024"}]},
    ]}
    t = M.parse_borrow(bybit, binance)
    assert t["AAA"] == (Decimal("0.00001"), "bybit")
    assert "BBB" not in t
    assert t["CCC"] == (Decimal("0.00001"), "binance")


async def test_settled_negative_rate_on_borrowable_coin_emits(clean, monkeypatch):
    _borrow(monkeypatch, {"NFCTEST": (Decimal("0.00001"), "binance")})
    seen: list = []
    _books(monkeypatch, DEEP, DEEP, seen)
    await _settled("-0.0040")
    drafts = await M.NegFundingCarry(symbols=[SYM]).generate()
    assert len(drafts) == 1
    d = drafts[0]
    assert d.side == "inverse_carry" and d.horizon_seconds == 172800
    assert d.context["funding_rate_8h"] == "-0.0040000000"
    assert d.context["borrow_rate_hourly"] == "0.00001"
    # the hedge leg is the borrow venue's spot pair
    assert ("binance", "spot", "NFCTESTUSDT") in seen
    assert d.context["spot_venue"] == "binance" and d.context["spot_symbol"] == "NFCTESTUSDT"
    f = d.context["entry_filter"]
    # 8 h interval -> 6 settlements in 48 h at 40 bps
    assert f["interval_h"] == 8 and f["expected_funding_bps"] == pytest.approx(240.0)
    # depth model: max(0.1 bps/h quote, floor at 40 bps/8h) x 48 x hold stress
    floor_h = M.depth_floor_hourly(Decimal("-0.0040"), 8) * 1e4
    assert f["borrow_stressed_bps"] == pytest.approx(max(0.1, floor_h) * 48 * M.HOLD_STRESS, rel=1e-3)
    assert f["borrow_flat_bps"] == pytest.approx(0.1 * 48 * M.BORROW_STRESS)
    assert f["leg_notional_usd"] == M.MAX_LEG_USD  # deep book: the $500 ceiling binds
    assert f["book_cost"]["fees_bps"] == pytest.approx(31.0)
    assert f["cost_share"] < M.MAX_COST_SHARE


async def test_missing_spot_book_never_emits(clean, monkeypatch):
    """No spot book -> no hedge -> no signal (never a naked long perp)."""
    _borrow(monkeypatch, {"NFCTEST": (Decimal("0.00001"), "bybit")})
    _books(monkeypatch, DEEP, None)
    await _settled("-0.0050")
    assert await M.NegFundingCarry(symbols=[SYM]).generate() == []


async def test_costly_book_or_borrow_skips(clean, monkeypatch):
    # 30 bps/8h = 180 bps over 48 h; a third is 60. Deep books: 31 fees + 4
    # impact + borrow at the depth floor (3.46 bps/8h x 6 = 20.8) = 55.8 -> trades.
    # A 16 bps spot spread -> ~72 -> not.
    _borrow(monkeypatch, {"NFCTEST": (Decimal("0.00001"), "bybit")})
    await _settled("-0.0030")
    _books(monkeypatch, DEEP, DEEP)
    assert len(await M.NegFundingCarry(symbols=[SYM]).generate()) == 1
    _books(monkeypatch, DEEP, _flat_book(2.0, 8.0, 5_000))
    assert await M.NegFundingCarry(symbols=[SYM]).generate() == []
    # 40 bps/8h, cheap books, but borrow of 2 bps/h x 48 = 96 bps (+35 > 80)
    await _settled("-0.0040")
    _borrow(monkeypatch, {"NFCTEST": (Decimal("0.0002"), "bybit")})
    _books(monkeypatch, DEEP, DEEP)
    assert await M.NegFundingCarry(symbols=[SYM]).generate() == []


def test_walk_and_cap_on_a_known_ladder():
    b = _flat_book(100.0, 1.0, 1_000, step_bps=2.0)  # levels at 1, 3, 5, 7 ... bps
    assert M.walk_bps(b.asks, b.mid, 1_000) == pytest.approx(1.0, rel=1e-6)
    assert M.walk_bps(b.asks, b.mid, 2_000) == pytest.approx(2.0, rel=1e-3)
    assert M.walk_bps(b.asks, b.mid, 1e9) is None  # deeper than the book
    cap = M.max_usd_within(b.asks, b.mid, 4.0)
    assert M.walk_bps(b.asks, b.mid, cap) == pytest.approx(4.0, rel=1e-3)
    # half-spread alone above the limit -> nothing can be done within it
    wide = _flat_book(100.0, 12.0, 1_000)
    assert M.max_usd_within(wide.bids, wide.mid, 10.0) == 0.0
    assert M.leg_cap_usd(b, wide) == 0.0


def test_entry_verdict_threshold_is_a_third_of_expected_funding():
    cost = {"total_bps": 40.0, "fees_bps": 31.0}
    kw = dict(interval_h=8, hourly_borrow=Decimal("0.00001"), horizon_h=48, cost=cost,
              leg_usd=500, stress=3.0, max_share=1 / 3, borrow_model="flat")
    # borrow 0.1 bps/h x 48 x 3 = 14.4; + 40 = 54.4 -> needs expected >= 163.2
    ok, inp = M.entry_verdict(rate=Decimal("-0.0028"), **kw)  # 168 bps
    assert ok and inp["cost_share"] == pytest.approx(54.4 / 168, rel=1e-4)
    ok, inp = M.entry_verdict(rate=Decimal("-0.0027"), **kw)  # 162 bps
    assert not ok and inp["skip"] == "cost_share"
    # 1 h interval: the same settled rate pays 48 times
    ok, inp = M.entry_verdict(rate=Decimal("-0.0008"), **{**kw, "interval_h": 1})
    assert ok and inp["expected_funding_bps"] == pytest.approx(384.0)
    ok, inp = M.entry_verdict(rate=Decimal("-0.0100"), **{**kw, "leg_usd": 10})
    assert not ok and inp["skip"] == "book_too_thin"


def test_depth_borrow_model_floors_quote_and_drops_flat_stress():
    # floor: exp(0.082) x |f8|^0.341 bps/8h; at 40 bps/8h ~3.80 bps/8h = 0.475 bps/h
    assert M.depth_floor_hourly(Decimal("-0.0040"), 8) * 1e4 == pytest.approx(0.475, abs=0.005)
    # the same per-interval rate on a 1 h coin is 8x deeper per 8 h
    assert M.depth_floor_hourly(Decimal("-0.0040"), 1) > M.depth_floor_hourly(Decimal("-0.0040"), 8)
    cost = {"total_bps": 40.0, "fees_bps": 31.0}
    kw = dict(rate=Decimal("-0.0040"), interval_h=8, horizon_h=48, cost=cost, leg_usd=500,
              stress=3.0, max_share=1 / 3, borrow_model="depth", hold_stress=1.0)
    # a quote below the floor is lifted to it
    ok, inp = M.entry_verdict(hourly_borrow=Decimal("0.00001"), **kw)
    assert inp["borrow_stressed_bps"] == pytest.approx(inp["borrow_depth_floor_bps"], rel=1e-6)
    # a squeezed quote above the floor is charged x1, not x3: 0.8 bps/h x 48 = 38.4;
    # 38.4 + 40 <= 240 / 3 keeps, the flat x3 (115.2 + 40) would not
    ok, inp = M.entry_verdict(hourly_borrow=Decimal("0.00008"), **kw)
    assert ok and inp["borrow_stressed_bps"] == pytest.approx(38.4)
    assert not inp["flat_keep"] and inp["borrow_flat_bps"] == pytest.approx(115.2)


def test_interval_from_next_settlement():
    t = datetime(2026, 10, 9, 8, tzinfo=UTC)
    assert M.interval_hours(t, t + timedelta(hours=1)) == 1
    assert M.interval_hours(t, t + timedelta(hours=4, minutes=1)) == 4
    assert M.interval_hours(t, None) == 8


async def test_unborrowable_coin_never_emits(clean, monkeypatch):
    _borrow(monkeypatch, {"OTHER": (Decimal("0.00001"), "bybit")})
    await _settled("-0.0050")
    assert await M.NegFundingCarry(symbols=[SYM]).generate() == []


async def test_placeholder_and_shallow_rates_do_not_trigger(clean, monkeypatch):
    # The live predicted rate is deeply negative, but the SETTLED one is shallow.
    _borrow(monkeypatch, {"NFCTEST": (Decimal("0.00001"), "bybit")})
    await _settled("-0.0003")
    await _ticker(datetime.now(UTC), "-0.0100", datetime.now(UTC) + timedelta(hours=7))
    assert await M.NegFundingCarry(symbols=[SYM]).generate() == []


async def test_old_settlement_is_not_a_fresh_signal(clean, monkeypatch):
    _borrow(monkeypatch, {"NFCTEST": (Decimal("0.00001"), "bybit")})
    await _settled("-0.0020", minutes_ago=120)
    assert await M.NegFundingCarry(symbols=[SYM]).generate() == []


def test_run_length_counts_consecutive_deep_settlements():
    d = Decimal
    assert M.run_length([d("-0.005"), d("-0.0006"), d("0.0001"), d("-0.01")]) == 2
    assert M.run_length([d("-0.0004")]) == 0
    assert M.run_length([]) == 0


def test_decay_ratio_buckets_and_unknown_history():
    assert M.decay_ratio(Decimal("-0.0010"), 0) == M.DECAY_RATIO[(0, 0)]
    assert M.decay_ratio(Decimal("-0.0050"), 2) == M.DECAY_RATIO[(1, 2)]
    assert M.decay_ratio(Decimal("-0.0100"), 7) == M.DECAY_RATIO[(2, 3)]
    assert M.decay_ratio(Decimal("-0.0010"), None) == M.DECAY_RATIO[(0, 0)]  # unknown = fresh spike


def test_decay_verdict_recorded_and_gates_only_when_selected():
    cost = {"total_bps": 40.0, "fees_bps": 31.0}
    kw = dict(rate=Decimal("-0.0028"), interval_h=8, hourly_borrow=Decimal("0.00001"),
              horizon_h=48, cost=cost, leg_usd=500, stress=3.0, max_share=1 / 3, borrow_model="flat")
    # naive 168 bps keeps (54.4 <= 56); decayed 168 x 0.133 = 22.3 < 54.4
    ok, inp = M.entry_verdict(**kw, run=0, model="naive")
    assert ok and inp["naive_keep"] and not inp["decay_keep"]
    assert inp["expected_decayed_bps"] == pytest.approx(168 * M.DECAY_RATIO[(0, 1)], rel=1e-4)
    ok, inp = M.entry_verdict(**kw, run=0, model="decay")
    assert not ok and inp["skip"] == "cost_share"
    # a persistent squeeze decays less: 168 x 0.499 = 83.8 >= 54.4
    ok, inp = M.entry_verdict(**kw, run=5, model="decay", decay_max_share=1.0)
    assert ok and inp["decay_keep"] and inp["prior_run"] == 5


async def test_prior_settlements_excludes_the_signal_settlement(monkeypatch):
    s = datetime(2026, 10, 9, 14, tzinfo=UTC)
    ms = lambda h: str(int((s - timedelta(hours=h)).timestamp() * 1000))  # noqa: E731
    payload = {"result": {"list": [
        {"fundingRate": "-0.005", "fundingRateTimestamp": ms(0)},
        {"fundingRate": "-0.004", "fundingRateTimestamp": ms(1)},
        {"fundingRate": "0.0001", "fundingRateTimestamp": ms(2)},
    ]}}

    class R:
        def raise_for_status(self): ...
        def json(self): return payload

    class C:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): ...
        async def get(self, url): return R()

    monkeypatch.setattr(M.httpx, "AsyncClient", C)
    out = await _REAL_PRIOR("KAIAUSDT", s)
    assert out == [Decimal("-0.004"), Decimal("0.0001")]
    assert M.run_length(out) == 1
