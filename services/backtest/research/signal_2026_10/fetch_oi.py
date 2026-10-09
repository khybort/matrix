import csv, json, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
BASE="https://api.bybit.com/v5/market/open-interest"
START=1759276800000; END=int(time.time()*1000)
t=json.load(open("data/linear_tickers.json"))["result"]["list"]
syms=[x["symbol"] for x in sorted(t,key=lambda x:-float(x["turnover24h"])) if x["symbol"].endswith("USDT")][:200]
def get(q):
    for a in range(8):
        try:
            with urllib.request.urlopen(BASE+"?"+q,timeout=30) as r: j=json.loads(r.read())
            time.sleep(0.3)
            if j.get("retCode")==0: return j["result"]
        except Exception: pass
        time.sleep(2+3*a)
    raise RuntimeError(q)
def job(sym):
    out=[]; end=END; cur=""
    while True:
        res=get(f"category=linear&symbol={sym}&intervalTime=1h&startTime={START}&endTime={END}&limit=200"+(f"&cursor={cur}" if cur else ""))
        lst=res["list"]; out+=[(sym,x["timestamp"],x["openInterest"]) for x in lst]
        cur=res.get("nextPageCursor") or ""
        if not lst or not cur: break
    return out
w=csv.writer(open("data/oi_1h.csv","w",newline="")); w.writerow(["symbol","ts","oi"])
with ThreadPoolExecutor(3) as ex:
    for i,r in enumerate(ex.map(job,syms)):
        w.writerows(r)
        if i%20==0: print(i,len(r),flush=True)
print("done",flush=True)
