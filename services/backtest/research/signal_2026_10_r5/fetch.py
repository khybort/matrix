"""Round 5 fetch: DVOL, Bybit perp klines + funding, today's books, Deribit option trades (20:00-24:00 UTC daily).

usage: python3 fetch.py <data dir> {dvol|bybit|books|trades}
Every response is cached as a file; a cached file is never refetched.
"""

import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

D = Path(sys.argv[1])
D.mkdir(parents=True, exist_ok=True)
START = datetime(2021, 3, 24, tzinfo=timezone.utc)
END = datetime(2026, 10, 9, tzinfo=timezone.utc)
ms = lambda dt: int(dt.timestamp() * 1000)  # noqa: E731


def get(url, pause=0.35):
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "matrix-research/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                body = json.load(r)
            time.sleep(pause)
            return body
        except Exception as e:  # noqa: BLE001 — network retry
            wait = 5 * (attempt + 1)
            print(f"retry {attempt} {url[:120]} {e}; sleep {wait}", flush=True)
            time.sleep(wait)
    raise RuntimeError(url)


def dvol():
    for cur in ("BTC", "ETH"):
        out = D / f"dvol_{cur}.json"
        if out.exists():
            continue
        rows, t0 = [], ms(START)
        while t0 < ms(END):
            t1 = min(t0 + 900 * 3600_000, ms(END))
            r = get("https://www.deribit.com/api/v2/public/get_volatility_index_data"
                    f"?currency={cur}&start_timestamp={t0}&end_timestamp={t1}&resolution=3600")["result"]
            rows += r["data"]
            t0 = t1
        rows = sorted({r[0]: r for r in rows}.values())
        out.write_text(json.dumps(rows))
        print(cur, "dvol", len(rows), flush=True)


def bybit():
    for sym in ("BTCUSDT", "ETHUSDT"):
        out = D / f"kline_{sym}.json"
        if not out.exists():
            rows, t0 = {}, ms(datetime(2021, 1, 1, tzinfo=timezone.utc))
            while t0 < ms(END):
                t1 = t0 + 999 * 3600_000
                r = get("https://api.bybit.com/v5/market/kline?category=linear"
                        f"&symbol={sym}&interval=60&start={t0}&end={t1}&limit=1000")["result"]["list"]
                for k in r:
                    if t0 <= int(k[0]) <= t1:
                        rows[int(k[0])] = k
                t0 = t1 + 3600_000
            out.write_text(json.dumps([rows[k] for k in sorted(rows)]))
            print(sym, "klines", len(rows), flush=True)
        out = D / f"funding_{sym}.json"
        if not out.exists():
            rows, end = {}, ms(END)
            while True:
                r = get("https://api.bybit.com/v5/market/funding/history?category=linear"
                        f"&symbol={sym}&endTime={end}&limit=200")["result"]["list"]
                new = [x for x in r if int(x["fundingRateTimestamp"]) not in rows]
                for x in r:
                    rows[int(x["fundingRateTimestamp"])] = x
                if not new or min(rows) < ms(datetime(2021, 1, 1, tzinfo=timezone.utc)):
                    break
                end = min(rows) - 1
            out.write_text(json.dumps([rows[k] for k in sorted(rows)]))
            print(sym, "funding", len(rows), flush=True)


def books():
    for i in range(5):
        for sym in ("BTCUSDT", "ETHUSDT"):
            out = D / f"book_{sym}_{i}.json"
            if not out.exists():
                out.write_text(json.dumps(get(
                    f"https://api.bybit.com/v5/market/orderbook?category=linear&symbol={sym}&limit=200")["result"]))
        if i < 4:
            time.sleep(61)
    print("books done", flush=True)


def trades():
    td = D / "trades"
    td.mkdir(exist_ok=True)
    # optional worker span: fetch.py <dir> trades YYYY-MM-DD YYYY-MM-DD (cached files are skipped, spans may overlap)
    day = datetime.fromisoformat(sys.argv[3]).replace(tzinfo=timezone.utc) if len(sys.argv) > 3 else START
    stop = datetime.fromisoformat(sys.argv[4]).replace(tzinfo=timezone.utc) if len(sys.argv) > 4 else END
    while day < stop:
        for cur in ("BTC", "ETH"):
            out = td / f"{cur}_{day:%Y%m%d}.json"
            if out.exists():
                continue
            t0, t1 = ms(day + timedelta(hours=20)), ms(day + timedelta(hours=24))
            keep, cursor = [], t0
            while True:
                r = get("https://history.deribit.com/api/v2/public/get_last_trades_by_currency_and_time"
                        f"?currency={cur}&kind=option&start_timestamp={cursor}&end_timestamp={t1 - 1}"
                        "&count=1000&sorting=asc", pause=0.3)["result"]
                tr = r["trades"]
                keep += [[x["timestamp"], x["instrument_name"], x.get("iv"), x["index_price"], x["amount"],
                          x["trade_id"]] for x in tr]
                if not r["has_more"] or not tr:
                    break
                cursor = max(tr[-1]["timestamp"], cursor + 1) if tr[-1]["timestamp"] == cursor else tr[-1]["timestamp"]  # same-ms trades re-served; deduplicated by trade_id below
            keep = list({k[5]: k for k in keep}.values())
            out.write_text(json.dumps(keep))
        if day.day == 1:
            print(f"{day:%Y-%m-%d}", flush=True)
        day += timedelta(days=1)
    print("trades done", flush=True)


{"dvol": dvol, "bybit": bybit, "books": books, "trades": trades}[sys.argv[2]]()
