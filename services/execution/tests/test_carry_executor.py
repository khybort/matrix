"""CarryExecutor: the two-leg carry path (docs/wiki/carry-execution.md).

Testnet-mode tests run the real state machine against a mocked Bybit V5
(`FakeBybit`), so every request the executor would send is answered by the
fake: nothing reaches a network. Dry-run tests prove the executor cannot send
at all. The gate is the real `matrix_shared.live_gate` against the test
wallet (equity $10 000, max_position_pct 2 %: both legs together <= $200, so
<= $100 a leg).
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import parse_qsl

import httpx
import pytest
from sqlalchemy import select

import matrix_shared.carry_executor as CE
from matrix_shared import shared_session_scope
from matrix_shared.carry_executor import (
    Book,
    BorrowQuote,
    CarryExecutor,
    CarryIntent,
    InstrumentSpec,
    MemoryStore,
)
from matrix_shared.carry_venues import BinanceMarginRequests, HttpTransport, NeverSend, SignedRequest
from matrix_shared.models import Wallet


def _ladder(mid: float, side: int, n: int = 40, qty: float = 50.0, tick: float = 0.0005) -> list:
    return [(round(mid + side * (i + 1) * tick, 6), qty) for i in range(n)]


DEEP = Book(bids=_ladder(1.0, -1), asks=_ladder(1.0, +1), source="test")
SPEC = InstrumentSpec(qty_step=Decimal("1"), min_qty=Decimal("1"), tick=Decimal("0.0001"), min_notional=Decimal("5"))


async def _books(venue, category, symbol):
    return DEEP


async def _specs(venue, category, symbol):
    return SPEC


async def _quote(venue, coin):
    return BorrowQuote(hourly=Decimal("0.00005"), max_borrow=Decimal("1000000"), borrowable=True)


class FakeBybit:
    """Mocked Bybit V5 (UTA) answering exactly the endpoints the executor uses.
    `fills[(category, side)]` is a list of fill fractions, one per order."""

    def __init__(self, *, borrow_ok: bool = True, max_trade: str = "1000000", avail: str = "100000",
                 fills: dict | None = None):
        self.borrow_ok, self.max_trade, self.avail = borrow_ok, max_trade, avail
        self.fills = {k: list(v) for k, v in (fills or {}).items()}
        self.calls: list[SignedRequest] = []
        self.orders: dict[str, dict] = {}
        self.owed = self.held = self.perp = Decimal("0")

    def paths(self) -> list[str]:
        return [c.path + (f":{c.params.get('category')}:{c.params.get('side')}" if c.path == "/v5/order/create" else "")
                for c in self.calls]

    async def send(self, req: SignedRequest) -> dict:
        assert req.url.startswith("https://api-testnet.bybit.com/"), req.url
        assert "X-BAPI-SIGN" in req.headers
        self.calls.append(req)
        p, prm = req.path, req.params
        ok = {"retCode": 0, "retMsg": "OK", "result": {}}
        if p == "/v5/order/spot-borrow-check":
            return {"retCode": 0, "result": {"maxTradeQty": self.max_trade}}
        if p == "/v5/account/wallet-balance":
            return {"retCode": 0, "result": {"list": [{"totalAvailableBalance": self.avail, "coin": [
                {"coin": "TST", "borrowAmount": str(self.owed), "accruedInterest": "0",
                 "walletBalance": str(self.held)}]}]}}
        if p == "/v5/account/borrow":
            if not self.borrow_ok:
                return {"retCode": 3100326, "retMsg": "insufficient loanable amount"}
            self.owed += Decimal(prm["amount"])
            self.held += Decimal(prm["amount"])
            return ok
        if p == "/v5/account/repay":
            amt = min(Decimal(prm["amount"]), self.owed)
            self.owed -= amt
            self.held -= amt
            return ok
        if p == "/v5/order/create":
            frac = (self.fills.get((prm["category"], prm["side"])) or [1.0]).pop(0) if self.fills.get(
                (prm["category"], prm["side"])) else 1.0
            q = (Decimal(prm["qty"]) * Decimal(str(frac))).to_integral_value()
            sign = 1 if prm["side"] == "Buy" else -1
            if prm["category"] == "linear":
                self.perp += sign * q
            else:
                self.held += sign * q
            self.orders[prm["orderLinkId"]] = {"cumExecQty": str(q), "avgPrice": prm["price"] if q else "0",
                                               "cumExecFee": "0", "orderStatus": "Filled" if q else "Cancelled"}
            return {"retCode": 0, "result": {"orderId": f"oid-{len(self.orders)}"}}
        if p == "/v5/order/realtime":
            return {"retCode": 0, "result": {"list": [self.orders[prm["orderLinkId"]]]}}
        if p == "/v5/position/list":
            return {"retCode": 0, "result": {"list": [{"side": "Buy", "size": str(self.perp)}]}}
        raise AssertionError(f"unexpected endpoint {p}")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("BYBIT_API_KEY", "BYBIT_API_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("BYBIT_TESTNET", "true")
    monkeypatch.setenv("BYBIT_TESTNET_API_KEY", "test-key")
    monkeypatch.setenv("BYBIT_TESTNET_API_SECRET", "test-secret")
    monkeypatch.setenv("LIVE_CAPITAL_CAP_USD", "2000")
    CE.kill_switch_reset()
    yield
    CE.kill_switch_reset()


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "true")


def _intent(wallet_id, sid="test_nfc", version=1, venue="bybit", leg="500", **kw) -> CarryIntent:
    return CarryIntent(prediction_id=uuid.uuid4(), strategy_id=sid, strategy_version=version, wallet_id=wallet_id,
                       perp_symbol="TSTUSDT", spot_venue=venue, spot_symbol="TSTUSDT", leg_usd=Decimal(leg),
                       borrow_hourly=Decimal("0.00005"), **kw)


def _ex(fake=None, mode="testnet", **kw) -> CarryExecutor:
    alerts: list[tuple[str, str]] = []

    async def alert(level, msg):
        alerts.append((level, msg))

    ex = CarryExecutor(mode=mode, transport=fake, books=_books, specs=_specs, borrow_quote=_quote,
                       store=MemoryStore(), alert=alert, **kw)
    ex.alerts = alerts
    return ex


async def _cert(grant_cert):
    sid, _, v = await grant_cert()
    return sid, v


# ---------------------------------------------------------------- open


async def test_open_happy_path_leg_order_and_combined_cap(wallet_id, grant_cert, live):
    sid, v = await _cert(grant_cert)
    fake = FakeBybit()
    ex = _ex(fake)
    res = await ex.open(_intent(wallet_id, sid, v))

    assert res.status == "done", res.reasons
    assert res.gate["allowed"] is True
    # $500 requested, wallet allows 2 % x $10k = $200 for BOTH legs -> 100 a leg at ~1.0
    assert res.qty == Decimal("100")
    assert res.combined_usd == pytest.approx(200, rel=1e-6)
    seq = [p for p in fake.paths() if p != "/v5/order/realtime"]
    assert seq[:5] == ["/v5/order/spot-borrow-check", "/v5/account/wallet-balance", "/v5/account/borrow",
                       "/v5/order/create:spot:Sell", "/v5/order/create:linear:Buy"]
    assert res.reconcile["matches"] is True
    assert fake.perp == 100 and fake.owed == 100 and fake.held == 0
    # idempotency: every order carries a deterministic client id
    links = [c.params["orderLinkId"] for c in fake.calls if c.path == "/v5/order/create"]
    assert len(set(links)) == len(links) and all(len(x) <= 36 for x in links)
    assert ex.alerts == []


async def test_second_leg_failure_unwinds_first(wallet_id, grant_cert, live):
    sid, v = await _cert(grant_cert)
    fake = FakeBybit(fills={("linear", "Buy"): [0.0, 0.0]})
    ex = _ex(fake)
    res = await ex.open(_intent(wallet_id, sid, v))

    assert res.status == "unwound"
    seq = [p for p in fake.paths() if p != "/v5/order/realtime"]
    i_sell = seq.index("/v5/order/create:spot:Sell")
    assert seq[i_sell + 1:i_sell + 3] == ["/v5/order/create:linear:Buy", "/v5/order/create:linear:Buy"]
    assert "/v5/order/create:spot:Buy" in seq and seq[-1] == "/v5/account/repay"
    assert fake.perp == 0 and fake.owed == 0  # flat, nothing borrowed
    # unwind is a bounded-slippage IOC, not an unbounded market order
    buy = next(c for c in fake.calls if c.path == "/v5/order/create" and c.params["side"] == "Buy"
               and c.params["category"] == "spot")
    assert buy.params["timeInForce"] == "IOC" and Decimal(buy.params["price"]) <= Decimal("1.01")
    assert [lvl for lvl, _ in ex.alerts] == ["warning"]
    assert CE.kill_switch_state()["halted"] is False


async def test_unwind_failure_alerts_and_trips_kill_switch(wallet_id, grant_cert, live):
    sid, v = await _cert(grant_cert)
    fake = FakeBybit(fills={("linear", "Buy"): [0.0, 0.0], ("spot", "Buy"): [0.0, 0.0]})
    ex = _ex(fake)
    res = await ex.open(_intent(wallet_id, sid, v))

    assert res.status == "naked"
    assert ("critical" in [lvl for lvl, _ in ex.alerts]) and "NAKED SHORT" in ex.alerts[-1][1]
    assert CE.kill_switch_state()["halted"] is True
    async with shared_session_scope() as s:
        tripped = (await s.execute(select(Wallet.circuit_tripped_at).where(Wallet.id == wallet_id))).scalar_one()
    assert tripped is not None
    # the latch refuses the next open before any request
    fake2 = FakeBybit()
    again = await _ex(fake2).open(_intent(wallet_id, sid, v))
    assert again.status == "refused" and fake2.calls == []


async def test_borrow_rejected_aborts_before_any_trade(wallet_id, grant_cert, live):
    sid, v = await _cert(grant_cert)
    fake = FakeBybit(borrow_ok=False)
    res = await _ex(fake).open(_intent(wallet_id, sid, v))
    assert res.status == "aborted" and "borrow rejected" in res.reasons[-1]
    assert not any(c.path == "/v5/order/create" for c in fake.calls)

    fake = FakeBybit(max_trade="10")  # quota below the qty: not even a borrow
    res = await _ex(fake).open(_intent(wallet_id, sid, v))
    assert res.status == "aborted" and "borrow rejected" in res.reasons[-1]
    assert [c.path for c in fake.calls] == ["/v5/order/spot-borrow-check"]


async def test_borrow_quote_moved_aborts(wallet_id, grant_cert, live):
    sid, v = await _cert(grant_cert)

    async def dear(venue, coin):
        return BorrowQuote(hourly=Decimal("0.0002"), max_borrow=None, borrowable=True)

    fake = FakeBybit()
    ex = _ex(fake)
    ex.borrow_quote = dear
    res = await ex.open(_intent(wallet_id, sid, v))
    assert res.status == "aborted" and "borrow quote moved" in res.reasons[-1] and fake.calls == []


async def test_partial_spot_fill_hedges_what_sold_and_repays_rest(wallet_id, grant_cert, live):
    sid, v = await _cert(grant_cert)
    fake = FakeBybit(fills={("spot", "Sell"): [0.6]})
    res = await _ex(fake).open(_intent(wallet_id, sid, v))
    assert res.status == "partial" and res.qty == Decimal("60")
    assert fake.perp == 60 and fake.owed == 60 and fake.held == 0
    assert res.reconcile["matches"] is True


async def test_partial_perp_fill_retries_then_unwinds_excess_spot(wallet_id, grant_cert, live):
    sid, v = await _cert(grant_cert)
    fake = FakeBybit(fills={("linear", "Buy"): [0.5, 0.0]})
    res = await _ex(fake).open(_intent(wallet_id, sid, v))
    assert res.status == "partial" and res.qty == Decimal("50")
    creates = [p for p in fake.paths() if p.startswith("/v5/order/create")]
    assert creates == ["/v5/order/create:spot:Sell", "/v5/order/create:linear:Buy",
                       "/v5/order/create:linear:Buy", "/v5/order/create:spot:Buy"]
    assert fake.perp == 50
    assert fake.owed - max(fake.held, Decimal("0")) <= 51  # short ~= hedge (buyback grossed up for fee)


async def test_gate_refuses_without_certificate(wallet_id, live):
    fake = FakeBybit()
    res = await _ex(fake).open(_intent(wallet_id, sid="never_certified_nfc"))
    assert res.status == "refused"
    assert any("certificate" in r for r in res.gate["reasons"])
    assert fake.calls == []


async def test_gate_refuses_when_live_flag_off(wallet_id, grant_cert, monkeypatch):
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "false")
    sid, v = await _cert(grant_cert)
    fake = FakeBybit()
    res = await _ex(fake).open(_intent(wallet_id, sid, v))
    assert res.status == "refused" and fake.calls == []


async def test_binance_borrow_has_no_testnet(wallet_id, grant_cert, live):
    sid, v = await _cert(grant_cert)
    fake = FakeBybit()
    res = await _ex(fake).open(_intent(wallet_id, sid, v, venue="binance"))
    assert res.status == "refused" and "no testnet" in res.reasons[-1] and fake.calls == []


async def test_open_is_idempotent_per_prediction(wallet_id, grant_cert, live):
    sid, v = await _cert(grant_cert)
    fake = FakeBybit()
    ex = _ex(fake)
    it = _intent(wallet_id, sid, v)
    assert (await ex.open(it)).status == "done"
    n = len(fake.calls)
    again = await ex.open(it)
    assert again.status == "refused" and "idempotent" in again.reasons[-1] and len(fake.calls) == n


async def test_open_rate_limiter(wallet_id, grant_cert, live):
    sid, v = await _cert(grant_cert)
    fake = FakeBybit()
    ex = _ex(fake, max_opens_per_hour=1)
    assert (await ex.open(_intent(wallet_id, sid, v))).status == "done"
    n = len(fake.calls)
    res = await ex.open(_intent(wallet_id, sid, v))
    assert res.status == "refused" and "rate limit" in res.reasons[-1] and len(fake.calls) == n


# ---------------------------------------------------------------- close


async def test_close_happy_path_spot_first_then_perp_then_repay(wallet_id, grant_cert, live):
    sid, v = await _cert(grant_cert)
    fake = FakeBybit()
    ex = _ex(fake)
    it = _intent(wallet_id, sid, v)
    assert (await ex.open(it)).status == "done"
    fake.calls.clear()
    res = await ex.close(it, qty=Decimal("100"), opened_at=datetime.now(UTC) - timedelta(hours=48))
    assert res.status == "done", res.reasons
    seq = [p for p in fake.paths() if p not in ("/v5/order/realtime",)]
    assert seq[:4] == ["/v5/account/wallet-balance", "/v5/order/create:spot:Buy", "/v5/order/create:linear:Sell",
                       "/v5/account/repay"]
    perp_sell = next(c for c in fake.calls if c.path == "/v5/order/create" and c.params["category"] == "linear")
    assert perp_sell.params.get("reduceOnly") is True
    assert fake.perp == 0 and fake.owed == 0


async def test_close_perp_failure_after_buyback_is_naked_and_kills(wallet_id, grant_cert, live):
    sid, v = await _cert(grant_cert)
    fake = FakeBybit()
    ex = _ex(fake)
    it = _intent(wallet_id, sid, v)
    await ex.open(it)
    fake.fills[("linear", "Sell")] = [0.0, 0.0, 0.0]
    res = await ex.close(it, qty=Decimal("100"), opened_at=datetime.now(UTC) - timedelta(hours=2))
    assert res.status == "naked" and "NAKED LONG" in ex.alerts[-1][1]
    assert CE.kill_switch_state()["halted"] is True


async def test_close_spot_unfilled_defers_and_stays_hedged(wallet_id, grant_cert, live):
    sid, v = await _cert(grant_cert)
    fake = FakeBybit()
    ex = _ex(fake)
    it = _intent(wallet_id, sid, v)
    await ex.open(it)
    fake.fills[("spot", "Buy")] = [0.0, 0.0]
    res = await ex.close(it, qty=Decimal("100"), opened_at=datetime.now(UTC) - timedelta(hours=2))
    assert res.status == "aborted" and fake.perp == 100  # hedge untouched
    assert not any(c.params.get("category") == "linear" for c in fake.calls if c.path == "/v5/order/create"
                   and c.params.get("side") == "Sell")
    assert CE.kill_switch_state()["halted"] is False


# ---------------------------------------------------------------- dry-run


async def test_dry_run_never_sends(wallet_id, monkeypatch):
    """Default mode, any transport passed in is replaced, no socket opens."""
    monkeypatch.delenv("MATRIX_CARRY_EXEC_MODE", raising=False)

    async def no_network(*a, **k):
        raise AssertionError("dry-run opened a connection")

    monkeypatch.setattr(httpx.AsyncClient, "send", no_network)
    spy = FakeBybit()
    before = NeverSend.calls
    ex = _ex(spy, mode=None)
    assert ex.mode == "dry_run" and isinstance(ex.transport, NeverSend)
    it = _intent(wallet_id, paper={"perp_buy_bps": 2.0, "spot_sell_bps": 5.0})
    res = await ex.open(it)
    assert res.status == "done", res.reasons
    assert res.gate["allowed"] is False  # recorded, simulation continues
    close = await ex.close(it, qty=res.qty, opened_at=datetime.now(UTC) - timedelta(hours=48))
    assert close.status == "done"
    assert spy.calls == [] and NeverSend.calls == before and res.sent == 0 and close.sent == 0
    paths = [r["path"] for r in res.requests]
    assert paths[:3] == ["/v5/order/spot-borrow-check", "/v5/account/wallet-balance", "/v5/account/borrow"]
    assert paths.count("/v5/order/create") == 2
    # simulated on the book: ~100 units walk 2 levels of 50 at 1.0005/1.0010
    perp = next(f for f in res.legs if f.leg == "perp_buy")
    assert perp.avg_price == pytest.approx(1.00075) and perp.slippage_bps == pytest.approx(7.5)
    assert res.gap_bps()["perp_buy"] == pytest.approx(5.5)
    assert "carry_exec[dry_run] open TSTUSDT/bybit" in res.log_line()


async def test_dry_run_partial_fill_on_thin_book(wallet_id):
    thin = Book(bids=_ladder(1.0, -1, n=1, qty=30), asks=_ladder(1.0, +1, n=1, qty=30))

    async def books(venue, category, symbol):
        return DEEP if category == "linear" else thin

    ex = _ex(mode="dry_run")
    ex.books = books
    res = await ex.open(_intent(wallet_id))
    assert res.status == "partial" and res.qty == Decimal("30")


async def test_mirror_logs_dry_run_open_and_close(wallet_id, monkeypatch):
    lines: list[str] = []
    sink = CE.logger.add(lambda m: lines.append(m.record["message"]), level="INFO")
    ex = _ex(mode="dry_run")
    monkeypatch.setattr(CE, "_MIRROR", ex)
    pred = SimpleNamespace(
        id=uuid.uuid4(), strategy_id="neg_funding_carry", strategy_version=1, asset_class="crypto",
        symbol="TSTUSDT", context={"spot_venue": "binance", "spot_symbol": "TSTUSDT",
                                   "borrow_rate_hourly": "0.00005"})
    res = await CE.mirror_paper_open(prediction=pred, wallet_id=wallet_id, notional=Decimal("196.82"),
                                     book_open={"ceiling_usd": 500.0, "perp_buy_bps": 1.0, "spot_sell_bps": 9.0})
    assert res is not None and res.status == "done" and res.mode == "dry_run"
    assert any(x.startswith("carry_exec[dry_run] open TSTUSDT/binance") for x in lines)
    pos = SimpleNamespace(symbol="TSTUSDT", wallet_id=wallet_id, notional_usd=Decimal("196.82"),
                          opened_at=datetime.now(UTC) - timedelta(hours=48))
    closed = await CE.mirror_paper_close(prediction=pred, position=pos,
                                         book_close={"perp_sell_bps": 3.0, "spot_buy_bps": 9.0})
    assert closed is not None and closed.status == "done" and closed.qty == res.qty
    CE.logger.remove(sink)
    assert any(x.startswith("carry_exec[dry_run] close TSTUSDT/binance") for x in lines)


# ---------------------------------------------------------------- venues


async def test_http_transport_refuses_mainnet():
    req = SignedRequest("bybit", "POST", "https://api.bybit.com/v5/order/create", "/v5/order/create", {}, "{}", {})
    with pytest.raises(PermissionError):
        await HttpTransport().send(req)


def test_binance_signature_covers_exact_body():
    b = BinanceMarginRequests(base="https://api.binance.com", api_key="k", api_secret="s", clock=lambda: 1.7e9)
    req = b.borrow("TST", "100")
    qs, sig = req.body.rsplit("&signature=", 1)
    assert sig == hmac.new(b"s", qs.encode(), hashlib.sha256).hexdigest()
    assert dict(parse_qsl(qs))["type"] == "BORROW" and req.headers["X-MBX-APIKEY"] == "k"
