import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from ingestion.carry_watchlist import Candidate, _seconds_to_next, base_coin, select_watchlist, spot_leg
from ingestion.connectors.binance_spot import parse, streams_for
from ingestion.connectors.bybit import (
    BarEvent,
    BybitConnector,
    OrderBookEvent,
    TickerEvent,
    chunks,
    topics_for,
)

NOW = datetime(2026, 10, 9, 12, 40, tzinfo=UTC)


def c(sym, turnover, settled=None, predicted=None):
    d = lambda v: None if v is None else Decimal(v)  # noqa: E731
    return Candidate(sym, turnover, d(settled), d(predicted))


def syms(members):
    return [m.symbol for m in members]


def test_enters_on_settled_or_predicted_and_ranks_by_turnover():
    cands = [
        c("AUSDT", 10e6, settled="-0.0006"),
        c("BUSDT", 50e6, predicted="-0.0009"),  # warm before its first settlement
        c("CUSDT", 90e6, settled="-0.0004", predicted="-0.0001"),  # not deep enough
    ]
    assert syms(select_watchlist(cands, {}, set(), set(), NOW, n=5)) == ["BUSDT", "AUSDT"]


def test_cap_core_and_pinned():
    cands = [c(f"S{i}USDT", 100 - i, settled="-0.001") for i in range(5)]
    out = select_watchlist(cands, {}, {"XUSDT"}, {"S0USDT"}, NOW, n=3)
    # pinned first and never dropped; core universe never takes a slot
    assert syms(out) == ["XUSDT", "S1USDT", "S2USDT"]
    assert out[0].pinned and [m.rank for m in out] == [1, 2, 3]


def test_hysteresis_band_and_keep_window():
    old = NOW - timedelta(hours=10)
    recent = NOW - timedelta(hours=2)
    cands = [
        c("BANDUSDT", 1e6, settled="-0.0003"),  # between exit and enter: stays
        c("GONEUSDT", 1e6, settled="0.0001"),  # above exit, qualified long ago
        c("RECENTUSDT", 1e6, settled="0.0001"),  # above exit, qualified 2h ago
        c("NEWUSDT", 1e6, settled="-0.0003"),  # band only, not incumbent: no entry
    ]
    inc = {"BANDUSDT": old, "GONEUSDT": old, "RECENTUSDT": recent}
    out = select_watchlist(cands, inc, set(), set(), NOW, n=10, keep_h=6)
    assert sorted(syms(out)) == ["BANDUSDT", "RECENTUSDT"]
    assert {m.symbol: m.last_qualified_at for m in out}["BANDUSDT"] == old


def test_incumbent_bonus_prevents_rank_flap():
    cands = [c("INCUSDT", 9e6, settled="-0.001"), c("NEWUSDT", 10e6, settled="-0.001")]
    assert syms(select_watchlist(cands, {"INCUSDT": NOW}, set(), set(), NOW, n=1)) == ["INCUSDT"]
    assert syms(select_watchlist(cands, {}, set(), set(), NOW, n=1)) == ["NEWUSDT"]


def test_entering_beats_band_incumbent_at_cap():
    cands = [c("BANDUSDT", 99e6, settled="-0.0003"), c("HOTUSDT", 1e6, settled="-0.002")]
    out = select_watchlist(cands, {"BANDUSDT": NOW - timedelta(hours=20)}, set(), set(), NOW, n=1)
    assert syms(out) == ["HOTUSDT"]


def test_spot_leg_prefers_borrow_venue():
    assert base_coin("1000BTTUSDT") == "BTT"
    assert spot_leg("1000BTTUSDT", "bybit", {"BTTUSDT"}, set()) == ("bybit", "BTTUSDT")
    assert spot_leg("SANDUSDT", "binance", {"SANDUSDT"}, {"SANDUSDT"}) == ("binance", "SANDUSDT")
    assert spot_leg("OGNUSDT", "bybit", set(), {"OGNUSDT"}) == ("binance", "OGNUSDT")
    assert spot_leg("ZZZUSDT", "bybit", set(), set()) is None


def test_refresh_timing():
    assert _seconds_to_next(NOW.replace(minute=39), 40) == 60
    assert _seconds_to_next(NOW, 40) == 3600


def test_topics_and_chunks():
    t = topics_for(["AUSDT"], ("tickers", "kline.1"))
    assert t == ["tickers.AUSDT", "kline.1.AUSDT"]
    assert [len(p) for p in chunks(list(range(23)))] == [10, 10, 3]
    assert streams_for(["OGNUSDT"]) == ["ognusdt@miniTicker", "ognusdt@kline_1m", "ognusdt@depth20@1000ms"]


def test_bybit_spot_kline_and_symbol_guard():
    conn = BybitConnector(["AUSDT"], testnet=False, category="spot")
    assert conn.url.endswith("/v5/public/spot") and conn._exchange == "bybit-spot"
    k = {"start": 1760000000000, "open": "1", "high": "2", "low": "0.5", "close": "1.5", "volume": "10"}
    msg = {"topic": "kline.1.AUSDT", "data": [{**k, "confirm": False}, {**k, "confirm": True}]}
    evs = conn._handle_message(msg)
    assert len(evs) == 1 and isinstance(evs[0], BarEvent) and evs[0].asset_class == "crypto_spot"
    # a frame for a symbol no longer subscribed is dropped
    assert conn._handle_message({**msg, "topic": "kline.1.BUSDT"}) == []


def test_set_symbols_without_socket():
    conn = BybitConnector(["AUSDT", "BUSDT"], testnet=False)
    added, removed = asyncio.run(conn.set_symbols(["BUSDT", "CUSDT"]))
    assert (added, removed) == (["CUSDT"], ["AUSDT"])
    assert set(conn._books) == {"BUSDT", "CUSDT"} and conn._exchange == "bybit"
    asyncio.run(conn.set_symbols([]))
    assert not conn._has_symbols.is_set()


def test_binance_parse():
    t = parse({"e": "24hrMiniTicker", "E": 1760000000000, "s": "OGNUSDT", "c": "0.05",
               "v": "100", "q": "5"})
    assert isinstance(t, TickerEvent) and t.exchange == "binance-spot" and t.last_price == Decimal("0.05")
    k = {"t": 1760000000000, "o": "1", "h": "2", "l": "0.5", "c": "1.5", "v": "3", "x": False}
    assert parse({"e": "kline", "s": "OGNUSDT", "k": k}) is None
    b = parse({"e": "kline", "s": "OGNUSDT", "k": {**k, "x": True}})
    assert isinstance(b, BarEvent) and b.close == Decimal("1.5")


def test_binance_depth_names_symbol_from_stream():
    d = {"lastUpdateId": 1, "bids": [["0.05", "100"]], "asks": [["0.051", "80"]]}
    ob = parse(d, stream="ognusdt@depth20@1000ms")
    assert isinstance(ob, OrderBookEvent) and ob.symbol == "OGNUSDT" and ob.bids == [("0.05", "100")]
