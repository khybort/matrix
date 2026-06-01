"""Minimal Bybit V5 REST client for shadow/live order submission."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx
from loguru import logger

from matrix_shared.rate_limiter import TokenBucket

BYBIT_TESTNET_REST = "https://api-testnet.bybit.com"
BYBIT_MAINNET_REST = "https://api.bybit.com"
DEFAULT_RECV_WINDOW_MS = 5000
# Bybit linear order/create ≈ 10/s per UID; stay under to avoid 10006.
DEFAULT_RATE_LIMIT_PER_SEC = 8.0
BYBIT_RATE_LIMIT_RETCODE = 10006
MAX_RATE_LIMIT_RETRIES = 3


def _rate_limit_per_sec() -> float:
    raw = os.environ.get("BYBIT_RATE_LIMIT_PER_SEC", "").strip()
    if not raw:
        return DEFAULT_RATE_LIMIT_PER_SEC
    return max(1.0, float(raw))


@dataclass
class BybitOrder:
    category: str
    symbol: str
    side: str  # Buy | Sell
    order_type: str
    qty: Decimal
    price: Decimal | None = None
    time_in_force: str = "IOC"
    reduce_only: bool = False


@dataclass
class BybitOrderResult:
    ok: bool
    dry_run: bool
    order_id: str | None
    raw: dict[str, Any] = field(default_factory=dict)


def sign_request(
    *,
    api_key: str,
    api_secret: str,
    timestamp_ms: int,
    recv_window_ms: int,
    payload: str,
) -> str:
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


def _serialize_order(order: BybitOrder) -> dict[str, Any]:
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
    return body


def _backoff_seconds(resp: httpx.Response, attempt: int) -> float:
    reset = resp.headers.get("X-Bapi-Limit-Reset-Timestamp")
    if reset:
        try:
            wait_ms = int(reset) - int(time.time() * 1000)
            if wait_ms > 0:
                return min(wait_ms / 1000.0, 5.0)
        except (TypeError, ValueError):
            pass
    return min(0.5 * (2**attempt), 3.0)


class BybitV5Client:
    def __init__(
        self,
        *,
        testnet: bool = True,
        rate_limit_per_sec: float | None = None,
    ) -> None:
        self.testnet = testnet
        self.base_url = BYBIT_TESTNET_REST if testnet else BYBIT_MAINNET_REST
        rate = rate_limit_per_sec if rate_limit_per_sec is not None else _rate_limit_per_sec()
        self.rate_limiter = TokenBucket(rate_per_sec=rate, capacity=rate)
        self._client: httpx.AsyncClient | None = None

    @staticmethod
    def _credentials() -> tuple[str, str] | None:
        testnet = os.environ.get("BYBIT_TESTNET", "true").strip().lower() != "false"
        if testnet:
            key = os.environ.get("BYBIT_TESTNET_API_KEY", "").strip()
            secret = os.environ.get("BYBIT_TESTNET_API_SECRET", "").strip()
        else:
            key = os.environ.get("BYBIT_API_KEY", "").strip()
            secret = os.environ.get("BYBIT_API_SECRET", "").strip()
        if not key or not secret:
            return None
        return key, secret

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=15.0)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _signed_get(self, path_query: str) -> dict[str, Any]:
        creds = self._credentials()
        if creds is None:
            return {"retCode": -1, "retMsg": "missing credentials"}
        api_key, api_secret = creds
        if "?" in path_query:
            path, query = path_query.split("?", 1)
        else:
            path, query = path_query, ""
        await self.rate_limiter.take()
        headers = build_signed_headers(
            api_key=api_key, api_secret=api_secret, payload=query
        )
        client = await self._http()
        resp = await client.get(f"{path}?{query}" if query else path, headers=headers)
        return resp.json()

    async def place_order(self, order: BybitOrder) -> BybitOrderResult:
        creds = self._credentials()
        if creds is None:
            return BybitOrderResult(
                ok=False,
                dry_run=True,
                order_id=None,
                raw={"dry_run_reason": "missing Bybit credentials"},
            )
        api_key, api_secret = creds
        body = _serialize_order(order)
        payload = json.dumps(body, separators=(",", ":"), sort_keys=False)
        client = await self._http()

        data: dict[str, Any] = {}
        resp: httpx.Response | None = None
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            await self.rate_limiter.take()
            headers = build_signed_headers(
                api_key=api_key, api_secret=api_secret, payload=payload
            )
            resp = await client.post("/v5/order/create", content=payload, headers=headers)
            data = resp.json()
            if data.get("retCode") != BYBIT_RATE_LIMIT_RETCODE:
                break
            wait_s = _backoff_seconds(resp, attempt)
            logger.warning(
                "bybit_v5 rate limited (10006) symbol={} attempt={} sleep={:.2f}s",
                order.symbol,
                attempt + 1,
                wait_s,
            )
            if attempt >= MAX_RATE_LIMIT_RETRIES:
                break
            await asyncio.sleep(wait_s)

        ret_code = data.get("retCode")
        order_id = (data.get("result") or {}).get("orderId")
        limits = {}
        if resp is not None:
            limits = {
                k: resp.headers.get(k)
                for k in resp.headers
                if k.lower().startswith("x-bapi-limit")
            }
        logger.info(
            "bybit_v5 place_order retCode={} symbol={} side={} qty={} orderId={} limits={}",
            ret_code,
            order.symbol,
            order.side,
            order.qty,
            order_id,
            limits,
        )
        return BybitOrderResult(
            ok=ret_code == 0,
            dry_run=False,
            order_id=order_id,
            raw=data,
        )
