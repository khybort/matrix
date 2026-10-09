"""Public market-data fetchers: polite, cached, and window-exact.

Three lessons from 2026-10-09 are built in:
- **Rate limits are per host and enforced here**, not by each script's
  `time.sleep`. A minimum interval per host, retries with backoff on 429/5xx,
  `Retry-After` honoured, and Binance's `X-MBX-USED-WEIGHT-1M` read on every
  response (above `weight_soft` the fetcher waits for the next minute).
- **HTTP 418 is a ban, not a retry**: Binance answers 418 once an IP keeps
  going after 429s. The host is blocked for the rest of the process and
  `BannedError` is raised, so a fetch loop cannot dig the ban deeper (one
  round-3b agent was banned that way).
- **Kline windows are clipped**. Bybit's kline endpoint returns its newest
  bars at or before `end` even when every one of them predates `start` (a pair
  delisted before the window). Round 3 took that as coverage and skipped the
  Binance fallback. Every kline/funding fetcher here drops rows outside
  [start, end].

Responses for historical windows are cached on disk forever (they do not
change); live endpoints (order books) pass `cache=False`.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from matrix_shared.research.costs import parse_levels

DEFAULT_MIN_INTERVAL_S = {
    "api.bybit.com": 0.12,
    "api.binance.com": 0.25,
    "fapi.binance.com": 0.25,
    "www.okx.com": 0.25,
    "api.bitget.com": 0.15,
    "api.gateio.ws": 0.25,
}


class BannedError(RuntimeError):
    """The host answered 418 (IP ban). Nothing more is sent to it this process."""


class FetchError(RuntimeError):
    pass


Opener = Callable[[str, float], tuple[int, dict, bytes]]


def _urllib_opener(url: str, timeout: float) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": "matrix-research/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, e.read() or b""


class PoliteFetcher:
    def __init__(
        self,
        cache_dir: str | Path | None,
        *,
        min_interval_s: dict[str, float] | None = None,
        default_interval_s: float = 0.25,
        max_retries: int = 5,
        weight_soft: int = 900,
        timeout: float = 20.0,
        opener: Opener | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.intervals = {**DEFAULT_MIN_INTERVAL_S, **(min_interval_s or {})}
        self.default_interval_s = default_interval_s
        self.max_retries = max_retries
        self.weight_soft = weight_soft
        self.timeout = timeout
        self.opener = opener or _urllib_opener
        self.sleep, self.clock, self.wall = sleep, clock, wall
        self.banned: set[str] = set()
        self.requests = 0
        self.cache_hits = 0
        self._locks: dict[str, threading.Lock] = {}
        self._last: dict[str, float] = {}
        self._guard = threading.Lock()

    def _lock(self, host: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(host, threading.Lock())

    def _cache_path(self, url: str) -> Path | None:
        if not self.cache_dir:
            return None
        return self.cache_dir / (hashlib.sha256(url.encode()).hexdigest()[:32] + ".json.gz")

    def get_json(self, url: str, *, cache: bool = True):
        """Parsed JSON, or None on 400/404 (no such symbol/window)."""
        cp = self._cache_path(url) if cache else None
        if cp is not None and cp.exists():
            self.cache_hits += 1
            return json.loads(gzip.decompress(cp.read_bytes()))
        host = urlparse(url).netloc
        if host in self.banned:
            raise BannedError(f"{host} banned this process (HTTP 418 earlier)")
        backoff = 1.0
        for _ in range(self.max_retries + 1):
            with self._lock(host):
                gap = self.intervals.get(host, self.default_interval_s)
                wait = self._last.get(host, -1e18) + gap - self.clock()
                if wait > 0:
                    self.sleep(wait)
                try:
                    status, headers, body = self.opener(url, self.timeout)
                except OSError:
                    status, headers, body = 0, {}, b""
                self._last[host] = self.clock()
                self.requests += 1
                used = headers.get("x-mbx-used-weight-1m")
                if used is not None and int(used) >= self.weight_soft:
                    self.sleep(60 - self.wall() % 60 + 1)
            if status == 418:
                self.banned.add(host)
                raise BannedError(f"{host} answered 418 (IP ban) for {url}")
            if status in (400, 404):
                return None
            if status == 200:
                data = json.loads(body)
                if cp is not None:
                    tmp = cp.with_suffix(".tmp")
                    tmp.write_bytes(gzip.compress(json.dumps(data).encode()))
                    tmp.replace(cp)
                return data
            ra = headers.get("retry-after")
            self.sleep(float(ra) if ra and ra.replace(".", "", 1).isdigit() else backoff)
            backoff = min(backoff * 2, 60.0)
        raise FetchError(f"gave up after {self.max_retries + 1} attempts: {url}")


# ---- endpoints -------------------------------------------------------------
# Kline rows: (start_ms, open, high, low, close, volume, quote_turnover), ascending.


def clip(rows: list[tuple], start_ms: int, end_ms: int) -> list[tuple]:
    """Rows whose first field (start ms) lies in [start_ms, end_ms], deduped, ascending."""
    seen: dict[int, tuple] = {}
    for r in rows:
        if start_ms <= int(r[0]) <= end_ms:
            seen[int(r[0])] = r
    return [seen[k] for k in sorted(seen)]


def bybit_klines(
    f: PoliteFetcher, category: str, symbol: str, interval: str, start_ms: int, end_ms: int
) -> list[tuple]:
    """Bybit v5 klines (category linear|spot, interval '60', 'D', ...), paging back from end."""
    rows: list[tuple] = []
    end = end_ms
    while end >= start_ms:
        j = f.get_json(
            f"https://api.bybit.com/v5/market/kline?category={category}&symbol={symbol}"
            f"&interval={interval}&start={start_ms}&end={end}&limit=1000"
        )
        if not j or j.get("retCode") != 0 or not j["result"]["list"]:
            break
        page = j["result"]["list"]
        rows += [(int(x[0]), *(float(v) for v in x[1:7])) for x in page]
        oldest = min(int(x[0]) for x in page)
        if len(page) < 1000 or oldest <= start_ms:
            break
        end = oldest - 1
    return clip(rows, start_ms, end_ms)


def binance_spot_klines(
    f: PoliteFetcher, symbol: str, interval: str, start_ms: int, end_ms: int
) -> list[tuple]:
    """Binance spot klines (interval '1h', ...), paging forward from start."""
    rows: list[tuple] = []
    start = start_ms
    while start <= end_ms:
        j = f.get_json(
            f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}"
            f"&startTime={start}&endTime={end_ms}&limit=1000"
        )
        if not isinstance(j, list) or not j:
            break
        rows += [
            (
                int(x[0]),
                float(x[1]),
                float(x[2]),
                float(x[3]),
                float(x[4]),
                float(x[5]),
                float(x[7]),
            )
            for x in j
        ]
        if len(j) < 1000:
            break
        start = int(j[-1][0]) + 1
    return clip(rows, start_ms, end_ms)


def bybit_funding(
    f: PoliteFetcher, symbol: str, start_ms: int, end_ms: int
) -> list[tuple[int, float]]:
    """Settled funding (ts_ms, rate) for a Bybit linear perp, ascending, clipped."""
    rows: list[tuple] = []
    end = end_ms
    while end >= start_ms:
        j = f.get_json(
            f"https://api.bybit.com/v5/market/funding/history?category=linear&symbol={symbol}"
            f"&startTime={start_ms}&endTime={end}&limit=200"
        )
        if not j or j.get("retCode") != 0 or not j["result"]["list"]:
            break
        page = j["result"]["list"]
        rows += [(int(x["fundingRateTimestamp"]), float(x["fundingRate"])) for x in page]
        oldest = min(int(x["fundingRateTimestamp"]) for x in page)
        if len(page) < 200 or oldest <= start_ms:
            break
        end = oldest - 1
    return clip(rows, start_ms, end_ms)


def bybit_book(f: PoliteFetcher, category: str, symbol: str, limit: int = 200):
    """(bids, asks) now; never cached. None when the symbol has no book."""
    j = f.get_json(
        f"https://api.bybit.com/v5/market/orderbook?category={category}&symbol={symbol}&limit={limit}",
        cache=False,
    )
    r = (j or {}).get("result") or {}
    if not r.get("b") or not r.get("a"):
        return None
    return parse_levels(r["b"]), parse_levels(r["a"])


def binance_spot_book(f: PoliteFetcher, symbol: str, limit: int = 500):
    """(bids, asks) now; never cached. Weight 5 at limit<=100, 25 at 500, 50 at 1000."""
    j = f.get_json(
        f"https://api.binance.com/api/v3/depth?symbol={symbol}&limit={limit}", cache=False
    )
    if not j or not j.get("bids") or not j.get("asks"):
        return None
    return parse_levels(j["bids"]), parse_levels(j["asks"])
