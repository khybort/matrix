"""Round 4: today's books — walk cost per leg at $500/$5k/$50k and today's basis of every live quarterly (all venues).
Usage: python3 capacity.py <data_dir>"""
import datetime as dt, json, re, sys
from pathlib import Path
import research as R

snap = json.loads((Path(sys.argv[1]) / "books_today.json").read_text())
now = dt.datetime.fromisoformat(snap["fetched_utc"])
books = snap["books"]
spot = {(b["venue"], b["symbol"][:3].rstrip("-U")): b for b in books if b["kind"] == "spot"}
mid = lambda b: (b["bids"][0][0] + b["asks"][0][0]) / 2
MON = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


def expiry(sym):
    m = re.search(r"(\d{6})$", sym.replace("-", "_").split("_")[-1]) if re.search(r"\d{6}$", sym) else None
    if m:
        s = m.group(1); return dt.datetime(2000 + int(s[:2]), int(s[2:4]), int(s[4:]), 8, tzinfo=dt.timezone.utc)
    m = re.search(r"(\d{1,2})([A-Z]{3})(\d{2})$", sym)
    if m:
        return dt.datetime(2000 + int(m.group(3)), MON[m.group(2)], int(m.group(1)), 8, tzinfo=dt.timezone.utc)
    m = re.search(r"USD([HMUZ])(\d{2})$", sym)
    if m:  # bybit inverse quarterly: last Friday of the month
        import calendar
        y, mo = 2000 + int(m.group(2)), {"H": 3, "M": 6, "U": 9, "Z": 12}[m.group(1)]
        d = max(x for x in range(1, calendar.monthrange(y, mo)[1] + 1) if dt.date(y, mo, x).weekday() == 4)
        return dt.datetime(y, mo, d, 8, tzinfo=dt.timezone.utc)
    return None


print(f"books fetched {snap['fetched_utc']}")
print(f"{'venue':8} {'kind':22} {'symbol':18} {'DTE':>5} {'basis%':>7} {'ann%':>6} | sell walk bps $500/$5k/$50k | buy walk")
for b in books:
    e = expiry(b["symbol"])
    coin = re.match(r"[A-Z]+", b["symbol"]).group(0)
    coin = coin[:-4] if coin.endswith("USDT") else (coin[:-3] if coin.endswith("USD") else coin)
    if b["kind"] == "spot" or e is None or coin not in ("BTC", "ETH", "SOL", "XRP", "BNB", "ADA", "LINK", "LTC", "DOT", "BCH"):
        if b["kind"] == "spot":
            w = [R.walk(b, s, "buy") * 1e4 for s in R.SIZES]
            print(f"{b['venue']:8} {'spot':22} {b['symbol']:18} {'':>5} {'':>7} {'':>6} | buy  " + " / ".join(f"{x:.1f}" for x in w))
        continue
    s = spot.get(("binance", coin))
    dte = (e - now).total_seconds() / 86400
    if dte < 7:
        continue
    basis = mid(b) / mid(s) - 1 if s else float("nan")
    ws = [R.walk(b, x, "sell") * 1e4 for x in R.SIZES]; wb = [R.walk(b, x, "buy") * 1e4 for x in R.SIZES]
    print(f"{b['venue']:8} {b['kind']:22} {b['symbol']:18} {dte:5.0f} {basis*100:7.2f} {basis*365/dte*100:6.2f} | "
          + " / ".join(f"{x:.1f}" for x in ws) + " | " + " / ".join(f"{x:.1f}" for x in wb))
