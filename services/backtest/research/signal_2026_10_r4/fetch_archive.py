"""Round 4 data: Binance public archive (data.binance.vision) 1h klines + mark klines of every dated contract, spot 1h
klines, spot 1m on expiry days. Output: <out>/binance/*.pkl. Usage: python3 fetch_archive.py <out_dir>"""
import io, re, sys, time, zipfile, urllib.request, concurrent.futures as cf
from pathlib import Path
import pandas as pd

S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
DL = "https://data.binance.vision/"
OUT = Path(sys.argv[1]) / "binance"; OUT.mkdir(parents=True, exist_ok=True)
ALTS = ["ADA", "BCH", "BNB", "DOT", "LINK", "LTC", "SOL", "XRP"]
COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time", "qv", "n", "tb", "tq", "ig"]


def ls(prefix, keys=False):
    out, marker = [], ""
    while True:
        t = urllib.request.urlopen(f"{S3}?delimiter=/&prefix={prefix}&marker={marker}", timeout=60).read().decode()
        got = re.findall(r"<Key>([^<]*)</Key>" if keys else r"<Prefix>([^<]*)</Prefix>", t)
        if not keys:
            got = got[1:]
        out += got
        if "<IsTruncated>true" not in t:
            return out
        marker = re.findall(r"<NextMarker>([^<]*)</NextMarker>", t)[0] if "<NextMarker>" in t else got[-1]


def get_zip(key):
    for i in range(4):
        try:
            b = urllib.request.urlopen(DL + key, timeout=60).read()
            z = zipfile.ZipFile(io.BytesIO(b))
            raw = z.read(z.namelist()[0]).decode()
            lines = [l for l in raw.splitlines() if l and l[0].isdigit()]
            df = pd.read_csv(io.StringIO("\n".join(lines)), header=None).iloc[:, :12]
            df.columns = COLS[: df.shape[1]]
            return df
        except Exception as e:  # noqa: BLE001 — retry transient network errors
            err = e
            time.sleep(2 * (i + 1))
    print("FAIL", key, err)
    return None


def fetch_series(base, sym, kind, interval):
    """base like data/futures/um ; kind klines|markPriceKlines"""
    keys = [k for k in ls(f"{base}/monthly/{kind}/{sym}/{interval}/", keys=True) if k.endswith(".zip")]
    months = {re.search(r"(\d{4}-\d{2})\.zip$", k).group(1) for k in keys}
    dkeys = [k for k in ls(f"{base}/daily/{kind}/{sym}/{interval}/", keys=True) if k.endswith(".zip")]
    dkeys = [k for k in dkeys if re.search(r"(\d{4}-\d{2})-\d{2}\.zip$", k).group(1) not in months]
    return keys + dkeys


def norm(df):
    t = df["open_time"].astype("int64")
    t = t.where(t < 10**14, t // 1000)  # spot archive switched to microseconds in 2025
    return pd.DataFrame({"ts": pd.to_datetime(t, unit="ms", utc=True), "open": df.open.astype(float),
                         "high": df.high.astype(float), "low": df.low.astype(float), "close": df.close.astype(float),
                         "volume": df.volume.astype(float)})


def job(base, sym, kind, interval, name):
    p = OUT / f"{name}.pkl"
    if p.exists():
        return name, "cached"
    keys = fetch_series(base, sym, kind, interval)
    parts = [d for d in (get_zip(k) for k in keys) if d is not None and len(d)]
    if not parts:
        return name, "empty"
    df = norm(pd.concat(parts)).drop_duplicates("ts").sort_values("ts")
    df.to_pickle(p)
    return name, len(df)


def main():
    jobs = []
    for mkt, pre in (("um", ["BTCUSDT_", "ETHUSDT_"]), ("cm", ["BTCUSD_", "ETHUSD_"] + [a + "USD_" for a in ALTS])):
        base = f"data/futures/{mkt}"
        for p in pre:
            syms = [x.split("/")[-2] for x in ls(f"{base}/monthly/klines/{p}")]
            syms = [s for s in syms if re.search(r"_\d{6}$", s)]
            for s in syms:
                for kind in ("klines", "markPriceKlines"):
                    jobs.append((base, s, kind, "1h", f"{mkt}_{kind}_{s}"))
    for c in ["BTC", "ETH"] + ALTS:
        jobs.append(("data/spot", c + "USDT", "klines", "1h", f"spot_klines_{c}USDT"))
    print(len(jobs), "series", flush=True)
    with cf.ThreadPoolExecutor(6) as ex:
        for name, res in ex.map(lambda j: job(*j), jobs):
            print(name, res, flush=True)


if __name__ == "__main__":
    main()
