"""ingestion.liquidation_recorder: Bybit allLiquidation parsing (side = the
position liquidated), the USDT-perp universe, stable sharding and the minute
coverage rule."""

from __future__ import annotations

import time
from decimal import Decimal

from ingestion import liquidation_recorder as R
from ingestion.connectors.bybit import BybitConnector, LiquidationEvent


def _msg(side: str, sym: str = "ROSEUSDT") -> dict:
    return {"topic": f"allLiquidation.{sym}", "type": "snapshot", "ts": 1739502303204,
            "data": [{"T": 1739502302929, "s": sym, "S": side, "v": "20000", "p": "0.04499"}]}


def test_buy_is_a_long_liquidated_sell_a_short():
    c = BybitConnector(["ROSEUSDT"], testnet=False, topics=(R.TOPIC,))
    assert c.topics == ("allLiquidation",)
    (ev,) = c._handle_message(_msg("Buy"))
    assert isinstance(ev, LiquidationEvent) and ev.side == "long" and ev.exchange == "bybit"
    assert ev.size == Decimal("20000") and ev.price == Decimal("0.04499")
    assert ev.ts.timestamp() == 1739502302.929
    assert c._handle_message(_msg("Sell"))[0].side == "short"


def test_malformed_and_unsubscribed_are_dropped():
    c = BybitConnector(["ROSEUSDT"], testnet=False, topics=(R.TOPIC,))
    assert c._handle_message(_msg("Hold")) == []
    assert c._handle_message(_msg("Buy", "BTCUSDT")) == []  # not subscribed here


def test_usdt_perps_only_trading_linear_perpetuals():
    page = {"result": {"list": [
        {"symbol": "BTCUSDT", "status": "Trading", "quoteCoin": "USDT", "contractType": "LinearPerpetual"},
        {"symbol": "BTCPERP", "status": "Trading", "quoteCoin": "USDC", "contractType": "LinearPerpetual"},
        {"symbol": "BTC-26DEC26", "status": "Trading", "quoteCoin": "USDT", "contractType": "LinearFutures"},
        {"symbol": "OLDUSDT", "status": "Closed", "quoteCoin": "USDT", "contractType": "LinearPerpetual"},
    ]}}
    assert R.usdt_perps([page, page]) == ["BTCUSDT"]


def test_assign_keeps_shards_stable():
    shards, changed = R.assign([], ["A", "B", "C"], 2)
    assert shards == [["A", "B"], ["C"]] and changed == 2
    shards, changed = R.assign(shards, ["A", "C", "D", "E"], 2)
    # B leaves shard 0, D fills the emptiest (shard 0 then 1 tie -> first), E the other
    assert sorted(s for sh in shards for s in sh) == ["A", "C", "D", "E"]
    assert shards[0][0] == "A" and shards[1][0] == "C" and all(len(sh) <= 2 for sh in shards)
    same, changed = R.assign(shards, ["A", "C", "D", "E"], 2)
    assert same == shards and changed == 0


class _Conn:
    def __init__(self, started, up=True):
        self.session_started, self._up = started, up

    def is_up(self):
        return self._up


def test_minute_covered_only_if_every_socket_was_up_all_minute():
    m = 1_000_020.0 // 60 * 60
    assert R.covered_minute(m, [_Conn(m - 5), _Conn(m)])
    assert not R.covered_minute(m, [_Conn(m - 5), _Conn(m + 1)])  # reconnected inside the minute
    assert not R.covered_minute(m, [_Conn(m - 5), _Conn(m - 5, up=False)])  # stale now
    assert not R.covered_minute(m, [_Conn(None)])
    assert not R.covered_minute(m, [])


def test_is_up_needs_a_session_and_recent_frames():
    c = BybitConnector(["X"], testnet=False, topics=(R.TOPIC,))
    assert not c.is_up()
    c.session_started = time.time()
    c._last_rx = time.monotonic()
    assert c.is_up()
    c._last_rx = time.monotonic() - 120
    assert not c.is_up()
