"""Bybit V5 REST + WS connector for live order submission.

PHASE 5 STATUS: scaffolding. The HTTP client is wired, the auth signing
is implemented, the gate is called — but no `place_order` calls execute
against the real exchange until:
  1. LIVE_EXECUTION_ENABLED=true in the operator's env
  2. paper_trade_certificate granted for (strategy_id, asset_class, version)
  3. (and the entire composite gate in execution.safety passes)

Until then, this module's main entry point logs the would-be order with
a DRY-RUN tag and returns a stub response. That's how Phase 5 lands
without anyone accidentally hitting mainnet.

Bybit V5 endpoints:
    testnet REST: https://api-testnet.bybit.com
    mainnet REST: https://api.bybit.com
Auth: HMAC SHA256 over (timestamp + api_key + recv_window + queryString or body).
Header: X-BAPI-API-KEY, X-BAPI-SIGN, X-BAPI-TIMESTAMP, X-BAPI-RECV-WINDOW

Why HMAC + httpx + dry-run logic live in one file (no auth.py split):
keeping the entire "thing that could send a real order" surface in one
file makes the audit cheap. One file to read end-to-end, one file to
diff in a code review, one file that owns the rule "never hit the
network without `should_submit_live` having said yes first."
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
from loguru import logger

from execution.rate_limiter import TokenBucket
from execution.safety import should_submit_live

# ---------- endpoints ---------------------------------------------------

BYBIT_TESTNET_REST = "https://api-testnet.bybit.com"
BYBIT_MAINNET_REST = "https://api.bybit.com"

# Bybit's recommended recv_window. Tight enough to fail-fast on a
# clock-skewed node, wide enough to survive normal jitter.
DEFAULT_RECV_WINDOW_MS = 5000

# We default to ~10 req/s. Bybit's public allowance is higher per-key,
# but the LIVE gate cares more about bug-induced bursts than throughput;
# being conservative here is a feature.
DEFAULT_RATE_LIMIT_PER_SEC = 10.0


# ---------- data shapes -------------------------------------------------


@dataclass
class OrderRequest:
    category: str       # "linear" for perpetuals
    symbol: str         # "BTCUSDT"
    side: str           # "Buy" or "Sell"
    order_type: str     # "Market" or "Limit"
    qty: Decimal
    price: Decimal | None = None
    time_in_force: str = "IOC"
    reduce_only: bool = False
    take_profit: Decimal | None = None
    stop_loss: Decimal | None = None


@dataclass
class OrderResponse:
    ok: bool
    dry_run: bool
    order_id: str | None
    raw: dict[str, Any] = field(default_factory=dict)


# ---------- signing -----------------------------------------------------


def sign_request(
    *,
    api_key: str,
    api_secret: str,
    timestamp_ms: int,
    recv_window_ms: int,
    payload: str,
) -> str:
    """HMAC SHA256 hex digest matching Bybit V5 spec.

    Pre-image is `timestamp + api_key + recv_window + payload` where
    `payload` is the request body for POST (compact-JSON) or the URL-
    encoded query string for GET. Bybit returns 10004 if any character
    or ordering differs, so the caller passes the exact serialized
    body that will go on the wire.
    """
    pre_sign = f"{timestamp_ms}{api_key}{recv_window_ms}{payload}"
    return hmac.new(
        api_secret.encode("utf-8"),
        pre_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def build_signed_headers(
    *,
    api_key: str,
    api_secret: str,
    payload: str,
    recv_window_ms: int = DEFAULT_RECV_WINDOW_MS,
    timestamp_ms: int | None = None,
) -> dict[str, str]:
    """All four X-BAPI-* headers Bybit V5 requires for authenticated calls."""
    ts = timestamp_ms if timestamp_ms is not None else int(time.time() * 1000)
    sig = sign_request(
        api_key=api_key,
        api_secret=api_secret,
        timestamp_ms=ts,
        recv_window_ms=recv_window_ms,
        payload=payload,
    )
    return {
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-SIGN": sig,
        "X-BAPI-TIMESTAMP": str(ts),
        "X-BAPI-RECV-WINDOW": str(recv_window_ms),
        "Content-Type": "application/json",
    }


def _serialize_order(order: OrderRequest) -> dict[str, Any]:
    """Bybit-shaped JSON body. Decimals → strings so Bybit gets exact qty/price."""
    body: dict[str, Any] = {
        "category": order.category,
        "symbol": order.symbol,
        "side": order.side,
        "orderType": order.order_type,
        "qty": str(order.qty),
        "timeInForce": order.time_in_force,
    }
    if order.price is not None:
        body["price"] = str(order.price)
    if order.reduce_only:
        body["reduceOnly"] = True
    if order.take_profit is not None:
        body["takeProfit"] = str(order.take_profit)
    if order.stop_loss is not None:
        body["stopLoss"] = str(order.stop_loss)
    return body


# ---------- connector ---------------------------------------------------


class BybitConnector:
    """All outbound order-submission paths flow through this class.

    The single entry-point `place_order` is the ONLY place that may
    contact the exchange in a non-dry-run mode, and it cannot do so
    until `should_submit_live` returns `allowed=True`.

    Read-only methods (`get_wallet_balance`, `get_position`) still
    short-circuit to dry-run when credentials are missing — Phase 5
    readiness is a property of the env, not of which call you make.
    """

    def __init__(self, *, testnet: bool = True, rate_limit_per_sec: float = DEFAULT_RATE_LIMIT_PER_SEC):
        self.testnet = testnet
        self.base_url = BYBIT_TESTNET_REST if testnet else BYBIT_MAINNET_REST
        self.rate_limiter = TokenBucket(
            rate_per_sec=rate_limit_per_sec,
            capacity=rate_limit_per_sec,  # 1-second burst
        )
        self._client: httpx.AsyncClient | None = None
        self._cred_label = "BYBIT_API_KEY / BYBIT_API_SECRET"

    # ---- env / readiness ----

    @staticmethod
    def _live_enabled() -> bool:
        return os.environ.get("LIVE_EXECUTION_ENABLED", "false").strip().lower() == "true"

    def _credentials(self) -> tuple[str, str] | None:
        if self.testnet:
            key = os.environ.get("BYBIT_TESTNET_API_KEY", "").strip()
            secret = os.environ.get("BYBIT_TESTNET_API_SECRET", "").strip()
            label = "BYBIT_TESTNET_API_KEY / BYBIT_TESTNET_API_SECRET"
        else:
            key = os.environ.get("BYBIT_API_KEY", "").strip()
            secret = os.environ.get("BYBIT_API_SECRET", "").strip()
            label = "BYBIT_API_KEY / BYBIT_API_SECRET"
        if not key or not secret:
            self._cred_label = label
            return None
        return key, secret

    def _should_dry_run(self) -> tuple[bool, str]:
        """Decide whether this call must dry-run regardless of gate outcome.

        Returns (must_dry_run, reason). Order of checks mirrors the way
        an operator brings the system live: env flag flips first, creds
        appear second.
        """
        if not self._live_enabled():
            return True, "LIVE_EXECUTION_ENABLED is not 'true'"
        if self._credentials() is None:
            return True, f"{self._cred_label} missing"
        return False, ""

    # ---- http client lifecycle ----

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=10.0)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ---- primary entry point ----

    async def place_order(
        self,
        order: OrderRequest,
        *,
        # required for the gate; never optional
        strategy_id: str,
        asset_class: str,
        strategy_version: int,
        intended_notional_usd: Decimal,
        wallet_id: UUID,
    ) -> OrderResponse:
        """The ONLY entry point for outbound order submission.

        Order of operations is load-bearing:
          1. Rate limiter — even denied attempts cost a token, since a
             buggy strategy spamming denied submits is still a problem.
          2. should_submit_live — the composite gate from execution.safety.
             If it says no, we return immediately with `dry_run=True` and
             every reason recorded in `raw`.
          3. If the gate said yes BUT we lack creds / LIVE_EXECUTION_ENABLED
             is off, we DRY-RUN — log loudly, return the would-be payload,
             do not hit the network.
          4. Only if (gate allowed) AND (live enabled) AND (creds present)
             do we sign and POST /v5/order/create.
        """
        await self.rate_limiter.take()

        decision = await should_submit_live(
            strategy_id=strategy_id,
            asset_class=asset_class,
            strategy_version=strategy_version,
            intended_notional_usd=intended_notional_usd,
            wallet_id=wallet_id,
        )
        if not decision.allowed:
            logger.warning(
                "DRY-RUN (gate denied) symbol={} side={} qty={} reasons={}",
                order.symbol, order.side, order.qty, decision.reasons,
            )
            return OrderResponse(
                ok=False,
                dry_run=True,
                order_id=None,
                raw={
                    "denied_by_gate": True,
                    "reasons": decision.reasons,
                    "snapshot": decision.snapshot,
                    "would_be_payload": _serialize_order(order),
                },
            )

        body = _serialize_order(order)
        must_dry, why = self._should_dry_run()
        if must_dry:
            logger.warning(
                "DRY-RUN ({}): symbol={} side={} qty={} payload={}",
                why, order.symbol, order.side, order.qty, body,
            )
            return OrderResponse(
                ok=True,
                dry_run=True,
                order_id=None,
                raw={
                    "dry_run_reason": why,
                    "would_be_payload": body,
                    "endpoint": f"{self.base_url}/v5/order/create",
                },
            )

        # ----- real submission path -----
        creds = self._credentials()
        assert creds is not None  # _should_dry_run already confirmed
        api_key, api_secret = creds

        # Bybit signs the EXACT body bytes. Build the JSON string once
        # and reuse for both signing and the request to avoid drift.
        payload = json.dumps(body, separators=(",", ":"), sort_keys=False)
        headers = build_signed_headers(
            api_key=api_key,
            api_secret=api_secret,
            payload=payload,
        )
        client = await self._http()
        resp = await client.post("/v5/order/create", content=payload, headers=headers)
        data = resp.json()
        ret_code = data.get("retCode")
        order_id = (data.get("result") or {}).get("orderId")
        logger.info(
            "bybit place_order retCode={} symbol={} side={} qty={} orderId={}",
            ret_code, order.symbol, order.side, order.qty, order_id,
        )
        return OrderResponse(
            ok=ret_code == 0,
            dry_run=False,
            order_id=order_id,
            raw=data,
        )

    # ---- read-only stubs ----

    async def get_wallet_balance(self, *, dry_run: bool | None = None) -> dict[str, Any]:
        """Read-only sanity check used by the daemon heartbeat.

        Currently always dry-run; the LIVE path is wired but stays gated
        behind LIVE_EXECUTION_ENABLED + creds. Useful for Phase 5
        operational visibility — "is the broker reachable at all?".
        """
        await self.rate_limiter.take()
        force_dry = dry_run if dry_run is not None else False
        must_dry, why = self._should_dry_run()
        if force_dry or must_dry:
            return {
                "dry_run": True,
                "reason": why or "explicit dry_run requested",
                "endpoint": f"{self.base_url}/v5/account/wallet-balance",
            }

        creds = self._credentials()
        assert creds is not None
        api_key, api_secret = creds
        # Bybit GET signing: payload = url-encoded query string. UNIFIED account.
        query = "accountType=UNIFIED"
        headers = build_signed_headers(
            api_key=api_key,
            api_secret=api_secret,
            payload=query,
        )
        client = await self._http()
        resp = await client.get(f"/v5/account/wallet-balance?{query}", headers=headers)
        return resp.json()

    async def get_position(self, symbol: str, *, dry_run: bool | None = None) -> dict[str, Any]:
        await self.rate_limiter.take()
        force_dry = dry_run if dry_run is not None else False
        must_dry, why = self._should_dry_run()
        if force_dry or must_dry:
            return {
                "dry_run": True,
                "reason": why or "explicit dry_run requested",
                "symbol": symbol,
            }
        creds = self._credentials()
        assert creds is not None
        api_key, api_secret = creds
        query = f"category=linear&symbol={symbol}"
        headers = build_signed_headers(
            api_key=api_key,
            api_secret=api_secret,
            payload=query,
        )
        client = await self._http()
        resp = await client.get(f"/v5/position/list?{query}", headers=headers)
        return resp.json()

    async def cancel_order(
        self, order_id: str, symbol: str, *, dry_run: bool | None = None
    ) -> dict[str, Any]:
        await self.rate_limiter.take()
        force_dry = dry_run if dry_run is not None else False
        must_dry, why = self._should_dry_run()
        if force_dry or must_dry:
            return {
                "dry_run": True,
                "reason": why or "explicit dry_run requested",
                "would_cancel": {"order_id": order_id, "symbol": symbol},
            }
        creds = self._credentials()
        assert creds is not None
        api_key, api_secret = creds
        body = {"category": "linear", "symbol": symbol, "orderId": order_id}
        payload = json.dumps(body, separators=(",", ":"), sort_keys=False)
        headers = build_signed_headers(
            api_key=api_key,
            api_secret=api_secret,
            payload=payload,
        )
        client = await self._http()
        resp = await client.post("/v5/order/cancel", content=payload, headers=headers)
        return resp.json()
