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


def _borrow(monkeypatch, table):
    async def fake():
        return table
    monkeypatch.setattr(M, "borrow_rates", fake)


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
    await _settled("-0.0012")
    drafts = await M.NegFundingCarry(symbols=[SYM]).generate()
    assert len(drafts) == 1
    d = drafts[0]
    assert d.side == "inverse_carry" and d.horizon_seconds == 172800
    assert d.context["funding_rate_8h"] == "-0.0012000000"
    assert d.context["borrow_rate_hourly"] == "0.00001"


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
