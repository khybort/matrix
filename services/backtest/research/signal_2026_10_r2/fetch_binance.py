"""Binance USD-M 1m klines (data.binance.vision, public) for the panel: monthly files where whole
months are covered, daily files otherwise. Output: <out>/<SYM>.csv (open_time_ms, close).
  python3 -I fetch_binance.py <panel.txt> <out_dir>"""
import csv, io, os, sys, urllib.request, zipfile
from concurrent.futures import ThreadPoolExecutor

B = "https://data.binance.vision/data/futures/um/"
MONTHS = ["2026-05", "2026-06", "2026-07", "2026-08", "2026-09"]
DAYS = [f"2026-04-{d:02d}" for d in range(24, 31)] + [f"2026-10-{d:02d}" for d in range(1, 9)]


def get(url):
    for _ in range(4):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
        except Exception:
            pass
    return None


def rows(blob):
    z = zipfile.ZipFile(io.BytesIO(blob))
    for line in io.TextIOWrapper(z.open(z.namelist()[0])):
        p = line.split(",")
        if p[0].isdigit():
            yield int(p[0]), p[4]


def job(sym, out):
    urls = [f"{B}monthly/klines/{sym}/1m/{sym}-1m-{m}.zip" for m in MONTHS]
    urls += [f"{B}daily/klines/{sym}/1m/{sym}-1m-{d}.zip" for d in DAYS]
    got = 0
    with open(f"{out}/{sym}.csv", "w", newline="") as f:
        w = csv.writer(f)
        for u in urls:
            b = get(u)
            if b:
                got += 1
                w.writerows(rows(b))
    return sym, got, len(urls)


def main():
    syms, out = open(sys.argv[1]).read().split(), sys.argv[2]
    os.makedirs(out, exist_ok=True)
    with ThreadPoolExecutor(6) as ex:
        for r in ex.map(lambda s: job(s, out), syms):
            print(*r, flush=True)


main()
