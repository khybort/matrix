"""Round 3b: full public depth today for Bybit linear and each hedge venue used by an episode.

usage: python3 fetch_books.py <data_dir>
Reads <data_dir>/book_pairs.json ({"bybit": [sym...], "binance": [vsym...], ...}, written by research.py
episodes) and the venue instrument files; writes <data_dir>/books.pkl with sizes in BASE COIN units.
"""
import json, pickle, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

D = sys.argv[1]
pairs = json.load(open(f"{D}/book_pairs.json"))
okx_ct = {x["instId"]: float(x["ctVal"]) for x in json.load(open(f"{D}/okx_info.json"))["data"]}
gate_ct = {x["name"]: float(x["quanto_multiplier"]) for x in json.load(open(f"{D}/gate_info.json"))}


def get(u):
    for a in range(4):
        try:
            return json.loads(urllib.request.urlopen(u, timeout=15).read())
        except Exception:
            time.sleep(1 + a)


def lv(raw, mult=1.0):
    return [(float(p), float(q) * mult) for p, q, *_ in raw or [] if float(q) > 0]


def job(item):
    v, s = item
    b = a = None
    if v == "bybit":
        j = get(f"https://api.bybit.com/v5/market/orderbook?category=linear&symbol={s}&limit=500")
        if j and j.get("result"):
            b, a = lv(j["result"].get("b")), lv(j["result"].get("a"))
    elif v == "binance":
        j = get(f"https://fapi.binance.com/fapi/v1/depth?symbol={s}&limit=1000")
        if j and "bids" in j:
            b, a = lv(j["bids"]), lv(j["asks"])
    elif v == "okx":
        j = get(f"https://www.okx.com/api/v5/market/books?instId={s}&sz=400")
        if j and j.get("data"):
            m = okx_ct.get(s, 1.0)
            b, a = lv(j["data"][0]["bids"], m), lv(j["data"][0]["asks"], m)
    elif v == "bitget":
        j = get(f"https://api.bitget.com/api/v2/mix/market/merge-depth?symbol={s}&productType=usdt-futures&limit=max")
        if j and j.get("data"):
            b, a = lv(j["data"]["bids"]), lv(j["data"]["asks"])
    elif v == "gate":
        j = get(f"https://api.gateio.ws/api/v4/futures/usdt/order_book?contract={s}&limit=300")
        if j and "bids" in j:
            m = gate_ct.get(s, 1.0)
            b = [(float(x["p"]), float(x["s"]) * m) for x in j["bids"] if float(x["s"]) > 0]
            a = [(float(x["p"]), float(x["s"]) * m) for x in j["asks"] if float(x["s"]) > 0]
    time.sleep(0.15)
    return (v, s), ((b, a) if b and a else None)


items = [(v, s) for v, ss in pairs.items() for s in ss]
with ThreadPoolExecutor(6) as ex:
    rows = dict(ex.map(job, items))
pickle.dump({"fetched_utc": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()), "books": rows}, open(f"{D}/books.pkl", "wb"))
print(len(rows), "ok", sum(1 for r in rows.values() if r))
