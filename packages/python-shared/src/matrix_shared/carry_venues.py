"""Signed request builders and the one sending transport for the carry path.

A spot-hedged carry (neg_funding_carry: long Bybit perp, short spot borrowed
on Bybit spot margin or Binance cross margin) needs endpoints the single-leg
`bybit_v5` client does not have: borrow-quota checks, manual borrow and
repay, spot orders and order-status queries, on two venues.

Building and sending are split on purpose. `BybitV5Requests` and
`BinanceMarginRequests` only *build* the exact signed request (method, URL,
body bytes, headers) and never touch the network, so the dry-run executor
builds precisely what a live run would send. `HttpTransport` is the only
object in this module that can send, and it refuses every host outside
`SENDABLE_HOSTS` — today only Bybit's testnet. Mainnet is not a mode here:
Phase 5 adds its host by a human commit (docs/TRADING.md), not by an env
flag.

Venue facts checked 2026-10-09 against the public testnet endpoints:
- Bybit testnet (api-testnet.bybit.com) serves UTA spot margin:
  `/v5/spot-margin-trade/data` lists 45 borrowable coins, 38 spot pairs are
  `marginTrading=utaOnly`, and `/v5/order/spot-borrow-check`,
  `/v5/account/borrow`, `/v5/account/repay` answer (auth required).
- Binance spot testnet (testnet.binance.vision) has no `/sapi` margin
  endpoints (404). Binance cross margin is dry-run only.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit

import httpx

from matrix_shared.bybit_v5 import build_signed_headers

BYBIT_MAINNET = "https://api.bybit.com"
BYBIT_TESTNET = "https://api-testnet.bybit.com"
BINANCE_MAINNET = "https://api.binance.com"

# The only hosts HttpTransport will send to. Adding a mainnet host here is a
# Phase-5 human decision (docs/TRADING.md "Phased capital exposure").
SENDABLE_HOSTS: frozenset[str] = frozenset({"api-testnet.bybit.com"})

# Venues whose margin endpoints exist on a testnet we can send to.
TESTNET_MARGIN = {"bybit": True, "binance": False}

DRY_RUN_KEY = "DRYRUN"
DRY_RUN_SECRET = "dry-run-placeholder-secret"
RECV_WINDOW_MS = 5000


@dataclass(frozen=True, slots=True)
class SignedRequest:
    venue: str
    method: str  # GET | POST
    url: str  # full URL (GET carries its signed query)
    path: str
    params: dict[str, Any]  # what the request asks for, unsigned
    body: str | None = None  # exact body bytes for POST
    headers: dict[str, str] = field(default_factory=dict)

    def redacted(self) -> dict[str, Any]:
        """Loggable form: no key, no signature, no timestamp noise."""
        return {"venue": self.venue, "method": self.method, "path": self.path, "params": self.params}


class Transport(Protocol):
    async def send(self, req: SignedRequest) -> dict[str, Any]: ...


class NeverSend:
    """The dry-run transport: any attempt to send is a bug, loudly."""

    calls = 0

    async def send(self, req: SignedRequest) -> dict[str, Any]:
        NeverSend.calls += 1
        raise AssertionError(f"dry-run must never send ({req.method} {req.path})")


class HttpTransport:
    """Sends a SignedRequest as built. Refuses any host not in SENDABLE_HOSTS."""

    def __init__(self, timeout_s: float = 10.0) -> None:
        self._timeout = timeout_s
        self._client: httpx.AsyncClient | None = None

    async def send(self, req: SignedRequest) -> dict[str, Any]:
        host = urlsplit(req.url).hostname or ""
        if host not in SENDABLE_HOSTS:
            raise PermissionError(f"carry transport refuses host {host!r} (sendable: {sorted(SENDABLE_HOSTS)})")
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        if req.method == "GET":
            resp = await self._client.get(req.url, headers=req.headers)
        else:
            resp = await self._client.post(req.url, content=req.body, headers=req.headers)
        try:
            return resp.json()
        except ValueError:
            return {"retCode": -1, "retMsg": f"http {resp.status_code}: non-JSON body"}

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _ms(clock: Callable[[], float]) -> int:
    return int(clock() * 1000)


class BybitV5Requests:
    """Bybit V5 (UTA) requests for both carry legs. Signing: HMAC-SHA256 over
    timestamp + key + recv_window + (query string | compact JSON body)."""

    venue = "bybit"

    def __init__(self, *, base: str, api_key: str, api_secret: str, clock: Callable[[], float] = time.time):
        self.base = base
        self._key = api_key
        self._secret = api_secret
        self._clock = clock

    def get(self, path: str, params: dict[str, Any]) -> SignedRequest:
        query = urlencode(params)
        headers = build_signed_headers(api_key=self._key, api_secret=self._secret, payload=query,
                                       recv_window_ms=RECV_WINDOW_MS, timestamp_ms=_ms(self._clock))
        return SignedRequest(self.venue, "GET", f"{self.base}{path}?{query}", path, dict(params), None, headers)

    def post(self, path: str, body: dict[str, Any]) -> SignedRequest:
        payload = json.dumps(body, separators=(",", ":"))
        headers = build_signed_headers(api_key=self._key, api_secret=self._secret, payload=payload,
                                       recv_window_ms=RECV_WINDOW_MS, timestamp_ms=_ms(self._clock))
        return SignedRequest(self.venue, "POST", f"{self.base}{path}", path, dict(body), payload, headers)

    # --- pre-trade checks
    def borrow_check(self, symbol: str) -> SignedRequest:
        """Max sellable qty incl. what margin can borrow (`maxTradeQty`)."""
        return self.get("/v5/order/spot-borrow-check", {"category": "spot", "symbol": symbol, "side": "Sell"})

    def wallet(self, coin: str | None = None) -> SignedRequest:
        params = {"accountType": "UNIFIED"}
        if coin:
            params["coin"] = coin
        return self.get("/v5/account/wallet-balance", params)

    # --- borrow / repay
    def borrow(self, coin: str, amount: str) -> SignedRequest:
        return self.post("/v5/account/borrow", {"coin": coin, "amount": amount})

    def repay(self, coin: str, amount: str) -> SignedRequest:
        return self.post("/v5/account/repay", {"coin": coin, "amount": amount})

    # --- orders
    def order(self, *, category: str, symbol: str, side: str, qty: str, price: str, link_id: str,
              reduce_only: bool = False) -> SignedRequest:
        """Limit IOC at a protective price = a market order with bounded
        slippage. Spot sells the coin already borrowed (isLeverage 0), so an
        oversell is rejected instead of silently borrowing more."""
        body: dict[str, Any] = {
            "category": category, "symbol": symbol, "side": side, "orderType": "Limit",
            "qty": qty, "price": price, "timeInForce": "IOC", "orderLinkId": link_id,
        }
        if category == "spot":
            body["isLeverage"] = 0
        if reduce_only:
            body["reduceOnly"] = True
        return self.post("/v5/order/create", body)

    def order_status(self, *, category: str, link_id: str) -> SignedRequest:
        return self.get("/v5/order/realtime", {"category": category, "orderLinkId": link_id})

    def position(self, symbol: str) -> SignedRequest:
        return self.get("/v5/position/list", {"category": "linear", "symbol": symbol})


class BinanceMarginRequests:
    """Binance cross-margin requests (spot leg only; the perp is Bybit's).
    Signing: HMAC-SHA256 over the url-encoded params incl. timestamp and
    recvWindow, appended as `signature`; key in X-MBX-APIKEY."""

    venue = "binance"

    def __init__(self, *, base: str, api_key: str, api_secret: str, clock: Callable[[], float] = time.time):
        self.base = base
        self._key = api_key
        self._secret = api_secret
        self._clock = clock

    def _signed(self, method: str, path: str, params: dict[str, Any]) -> SignedRequest:
        full = {**params, "timestamp": _ms(self._clock), "recvWindow": RECV_WINDOW_MS}
        qs = urlencode(full)
        sig = hmac.new(self._secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
        signed = f"{qs}&signature={sig}"
        headers = {"X-MBX-APIKEY": self._key}
        if method == "GET":
            return SignedRequest(self.venue, "GET", f"{self.base}{path}?{signed}", path, dict(params), None, headers)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        return SignedRequest(self.venue, "POST", f"{self.base}{path}", path, dict(params), signed, headers)

    def max_borrowable(self, asset: str) -> SignedRequest:
        return self._signed("GET", "/sapi/v1/margin/maxBorrowable", {"asset": asset})

    def account(self) -> SignedRequest:
        return self._signed("GET", "/sapi/v1/margin/account", {})

    def borrow(self, asset: str, amount: str) -> SignedRequest:
        return self._signed("POST", "/sapi/v1/margin/borrow-repay",
                            {"asset": asset, "isIsolated": "FALSE", "amount": amount, "type": "BORROW"})

    def repay(self, asset: str, amount: str) -> SignedRequest:
        return self._signed("POST", "/sapi/v1/margin/borrow-repay",
                            {"asset": asset, "isIsolated": "FALSE", "amount": amount, "type": "REPAY"})

    def order(self, *, symbol: str, side: str, qty: str, price: str, link_id: str) -> SignedRequest:
        return self._signed("POST", "/sapi/v1/margin/order", {
            "symbol": symbol, "side": side.upper(), "type": "LIMIT", "timeInForce": "IOC",
            "quantity": qty, "price": price, "newClientOrderId": link_id,
            "sideEffectType": "NO_SIDE_EFFECT", "newOrderRespType": "FULL",
        })

    def order_status(self, *, symbol: str, link_id: str) -> SignedRequest:
        return self._signed("GET", "/sapi/v1/margin/order", {"symbol": symbol, "origClientOrderId": link_id})
