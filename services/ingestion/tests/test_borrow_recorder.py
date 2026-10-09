"""ingestion.borrow_recorder: parse both public tables, write on change or heartbeat."""

from __future__ import annotations

from decimal import Decimal

from ingestion import borrow_recorder as R

BYBIT = {"result": {"vipCoinList": [{"list": [
    {"currency": "AAA", "borrowable": True, "hourlyBorrowRate": "0.00001", "maxBorrowingAmount": "5000"},
    {"currency": "BBB", "borrowable": False, "hourlyBorrowRate": "0.00003", "maxBorrowingAmount": "0"},
    {"currency": "CCC", "borrowable": True, "hourlyBorrowRate": ""},
]}]}}
BINANCE = {"data": [
    {"assetName": "AAA", "specs": [{"vipLevel": "1", "dailyInterestRate": "0.1"},
                                   {"vipLevel": "0", "dailyInterestRate": "0.00048", "borrowLimit": "37000"}]},
    {"assetName": "DDD", "specs": []},
]}


def test_parse_keeps_every_coin_with_a_rate_per_venue():
    q = R.parse_tables(BYBIT, BINANCE)
    assert q[("bybit", "AAA")] == (Decimal("0.00001"), Decimal("5000"), True)
    assert q[("bybit", "BBB")][2] is False  # recorded, flagged not borrowable
    assert ("bybit", "CCC") not in q and ("binance", "DDD") not in q
    assert q[("binance", "AAA")] == (Decimal("0.00002"), Decimal("37000"), True)  # VIP0 daily / 24
    assert R.parse_tables(None, BINANCE).keys() == {("binance", "AAA")}  # one venue down


def test_due_on_change_or_heartbeat():
    q = R.parse_tables(BYBIT, None)
    k = ("bybit", "AAA")
    assert set(R.due(q, {}, 0.0)) == set(q)
    last = {key: (val, 1000.0) for key, val in q.items()}
    assert R.due(q, last, 1000.0 + 600) == []
    assert set(R.due(q, last, 1000.0 + R.HEARTBEAT_S)) == set(q)
    changed = {**q, k: (Decimal("0.00002"), Decimal("5000"), True)}
    assert R.due(changed, last, 1000.0 + 600) == [k]
