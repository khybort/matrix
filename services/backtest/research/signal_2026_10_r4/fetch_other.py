"""Round 4 data outside the Binance archive: Deribit quarterly 1h candles + delivery prices, Binance delivery prices,
OKX USDT lending-rate history, spot 1m klines on expiry days (archive), and today's books (Binance spot/USD-M/COIN-M,
Deribit, Bybit, OKX). Slow on purpose: >= 1 s between Binance API calls. Usage: python3 fetch_other.py <out_dir> [parts]"""
import calendar, datetime as dt, io, json, sys, time, urllib.request, zipfile
from pathlib import Path
import pandas as pd

OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
PARTS = set(sys.argv[2].split(",")) if len(sys.argv) > 2 else {"deribit", "delivery", "okx", "spot1m", "books"}
ALTS = ["ADA", "BCH", "BNB", "DOT", "LINK", "LTC", "SOL", "XRP"]
UA = {"User-Agent": "Mozilla/5.0"}


def get(url, tries=4):
    for i in range(tries):
        try:
            return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read())
        except Exception as e:  # noqa: BLE001
            err = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"{url}: {err}")


def quarterly_expiries(y0=2019, y1=2027):
    out = []
    for y in range(y0, y1 + 1):
        for m in (3, 6, 9, 12):
            last = max(d for d in range(1, calendar.monthrange(y, m)[1] + 1) if dt.date(y, m, d).weekday() == 4)
            out.append(dt.datetime(y, m, last, 8, tzinfo=dt.timezone.utc))
    return out


def deribit():
    rows = []
    for cur in ("BTC", "ETH"):
        for e in quarterly_expiries(2020, 2027):
            name = f"{cur}-{e.day}{e.strftime('%b').upper()}{e.strftime('%y')}"
            end = min(int(e.timestamp() * 1000), int(time.time() * 1000))
            t = end - 400 * 86400_000
            while t < end:
                t2 = min(t + 150 * 86400_000, end)
                try:
                    r = get(f"https://www.deribit.com/api/v2/public/get_tradingview_chart_data?instrument_name={name}"
                            f"&start_timestamp={t}&end_timestamp={t2}&resolution=60", tries=2)["result"]
                except RuntimeError:  # instrument never listed (e.g. a future quarter not yet created)
                    break
                if r.get("status") == "ok" and r.get("ticks"):
                    df = pd.DataFrame({k: r[k] for k in ("ticks", "open", "high", "low", "close", "volume")})
                    df["instrument"] = name
                    df["expiry"] = e
                    rows.append(df)
                t = t2
                time.sleep(0.25)
            print("deribit", name, sum(len(x) for x in rows if x.instrument.iat[0] == name), flush=True)
    df = pd.concat(rows)
    df = df[df.volume > 0].copy() if False else df  # keep all bars; zero-volume bars are filtered in research
    df["ts"] = pd.to_datetime(df.ticks, unit="ms", utc=True)
    df.drop_duplicates(["instrument", "ts"]).to_pickle(OUT / "deribit_1h.pkl")
    dp = []
    for idx in ("btc_usd", "eth_usd"):
        off = 0
        while True:
            r = get(f"https://www.deribit.com/api/v2/public/get_delivery_prices?index_name={idx}&offset={off}&count=1000")
            d = r["result"]["data"]
            for x in d:
                dp.append({"index": idx, "date": x["date"], "delivery_price": x["delivery_price"]})
            off += len(d)
            if not d or off >= r["result"]["records_total"]:
                break
            time.sleep(0.3)
    pd.DataFrame(dp).to_pickle(OUT / "deribit_delivery.pkl")
    print("deribit delivery", len(dp), flush=True)


def delivery():
    rows = []
    for host, pairs in (("https://fapi.binance.com", ["BTCUSDT", "ETHUSDT"]),
                        ("https://dapi.binance.com", ["BTCUSD", "ETHUSD"] + [a + "USD" for a in ALTS])):
        for p in pairs:
            for x in get(f"{host}/futures/data/delivery-price?pair={p}"):
                rows.append({"pair": p, "host": host, "deliveryTime": x["deliveryTime"], "deliveryPrice": x["deliveryPrice"]})
            print("delivery", p, flush=True)
            time.sleep(1.5)
    pd.DataFrame(rows).to_pickle(OUT / "binance_delivery.pkl")


def okx():
    rows, after = [], int(time.time() * 1000)
    while True:
        d = get(f"https://www.okx.com/api/v5/finance/savings/lending-rate-history?ccy=USDT&limit=100&after={after}")["data"]
        if not d:
            break
        rows += d
        after = int(d[-1]["ts"])
        time.sleep(0.25)
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df.ts.astype("int64"), unit="ms", utc=True)
    df["lendingRate"] = df.lendingRate.astype(float)
    df["rate"] = df.rate.astype(float)
    df.drop_duplicates("ts").sort_values("ts").to_pickle(OUT / "okx_usdt_lending.pkl")
    print("okx lending", len(df), df.ts.min(), df.ts.max(), flush=True)


def spot1m():
    rows = []
    days = sorted({e.date() for e in quarterly_expiries(2020, 2026) if e < dt.datetime.now(dt.timezone.utc)})
    for c in ["BTC", "ETH"] + ALTS:
        for d in days:
            key = f"data/spot/daily/klines/{c}USDT/1m/{c}USDT-1m-{d.isoformat()}.zip"
            try:
                b = urllib.request.urlopen("https://data.binance.vision/" + key, timeout=60).read()
            except Exception:  # noqa: BLE001 — missing day (coin not listed yet)
                continue
            z = zipfile.ZipFile(io.BytesIO(b))
            raw = [l for l in z.read(z.namelist()[0]).decode().splitlines() if l and l[0].isdigit()]
            df = pd.read_csv(io.StringIO("\n".join(raw)), header=None).iloc[:, :5]
            df.columns = ["open_time", "open", "high", "low", "close"]
            t = df.open_time.astype("int64"); t = t.where(t < 10**14, t // 1000)
            df["ts"] = pd.to_datetime(t, unit="ms", utc=True); df["coin"] = c
            rows.append(df[["coin", "ts", "close"]])
        print("spot1m", c, flush=True)
    pd.concat(rows).to_pickle(OUT / "spot_1m_expiry_days.pkl")


def books():
    snap = {"fetched_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "books": []}

    def add(venue, kind, sym, bids, asks, unit, ct=1.0):
        snap["books"].append({"venue": venue, "kind": kind, "symbol": sym, "unit": unit, "ct": ct,
                              "bids": [[float(p), float(q)] for p, q, *_ in bids],
                              "asks": [[float(p), float(q)] for p, q, *_ in asks]})

    for c in ["BTC", "ETH"] + ALTS:
        d = get(f"https://api.binance.com/api/v3/depth?symbol={c}USDT&limit=500"); time.sleep(1.5)
        add("binance", "spot", c + "USDT", d["bids"], d["asks"], "coin")
    for x in get("https://fapi.binance.com/fapi/v1/exchangeInfo")["symbols"]:
        if x["contractType"] in ("CURRENT_QUARTER", "NEXT_QUARTER") and x["status"] == "TRADING":
            time.sleep(1.5)
            d = get(f"https://fapi.binance.com/fapi/v1/depth?symbol={x['symbol']}&limit=100")
            add("binance", "um", x["symbol"], d["bids"], d["asks"], "coin")
    time.sleep(1.5)
    for x in get("https://dapi.binance.com/dapi/v1/exchangeInfo")["symbols"]:
        if x["contractType"] in ("CURRENT_QUARTER", "NEXT_QUARTER") and x["contractStatus"] == "TRADING":
            time.sleep(1.5)
            d = get(f"https://dapi.binance.com/dapi/v1/depth?symbol={x['symbol']}&limit=100")
            add("binance", "cm", x["symbol"], d["bids"], d["asks"], "usd_contracts", float(x["contractSize"]))
    for cur in ("BTC", "ETH"):
        for x in get(f"https://www.deribit.com/api/v2/public/get_instruments?currency={cur}&kind=future")["result"]:
            if x["settlement_period"] in ("quarter", "month") or x["instrument_name"].endswith("PERPETUAL"):
                d = get(f"https://www.deribit.com/api/v2/public/get_order_book?instrument_name={x['instrument_name']}&depth=1000")
                r = d["result"]
                add("deribit", x["settlement_period"], x["instrument_name"], r["bids"], r["asks"], "usd")
                snap["books"][-1]["index_price"] = r.get("index_price")
                time.sleep(0.25)
    for cat in ("linear", "inverse"):
        for x in get(f"https://api.bybit.com/v5/market/instruments-info?category={cat}&limit=1000")["result"]["list"]:
            if x.get("contractType", "").endswith("Futures") and "Perpetual" not in x["contractType"] and \
                    x["baseCoin"] in ("BTC", "ETH"):
                d = get(f"https://api.bybit.com/v5/market/orderbook?category={cat}&symbol={x['symbol']}&limit=200")["result"]
                add("bybit", cat, x["symbol"], d["b"], d["a"], "coin" if cat == "linear" else "usd")
                time.sleep(0.3)
    for c in ("BTC", "ETH"):
        d = get(f"https://api.bybit.com/v5/market/orderbook?category=spot&symbol={c}USDT&limit=200")["result"]
        add("bybit", "spot", c + "USDT", d["b"], d["a"], "coin")
        d = get(f"https://www.okx.com/api/v5/market/books?instId={c}-USDT&sz=400")["data"][0]
        add("okx", "spot", c + "-USDT", d["bids"], d["asks"], "coin")
        time.sleep(0.3)
    for x in get("https://www.okx.com/api/v5/public/instruments?instType=FUTURES")["data"]:
        if x["uly"] in ("BTC-USD", "ETH-USD", "BTC-USDT", "ETH-USDT") and x["alias"] in ("quarter", "next_quarter"):
            d = get(f"https://www.okx.com/api/v5/market/books?instId={x['instId']}&sz=400")["data"][0]
            add("okx", f"{x['ctType']}_{x['settleCcy']}_{x['alias']}", x["instId"], d["bids"], d["asks"],
                "usd_contracts" if x["ctType"] == "inverse" else "coin_contracts", float(x["ctVal"]))
            time.sleep(0.3)
    (OUT / "books_today.json").write_text(json.dumps(snap))
    print("books", len(snap["books"]), flush=True)


for part in ("okx", "delivery", "books", "spot1m", "deribit"):
    if part in PARTS:
        globals()[part]()
