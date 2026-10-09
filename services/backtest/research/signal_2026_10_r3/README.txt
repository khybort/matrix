Signal research 2026-10, round 3: positive-funding mirror of H1 (short perp + long spot, no borrow).
Write-up: docs/wiki/signal-research-2026-10.md "Round 3: positive-funding mirror". Pre-registration and
timestamped log: PREREG.txt (committed alone in 8346dbd before any return was computed).

Host python3 + pandas/numpy (no scipy needed). DATA = round 1's data dir (funding.csv, kline_1h.csv,
premium_1h.csv; active_universe.txt one level up), WORK = any scratch dir.
  python3 fetch_spot.py  DATA WORK [active_universe.txt]   # 1h spot klines, Bybit then Binance; resumable
  python3 fetch_books.py DATA WORK [active_universe.txt]   # today's books, walked at $500 / $5k per leg
  python3 research.py DATA WORK train ; python3 research.py DATA WORK holdout ; python3 research.py DATA WORK ref
  python3 describe.py WORK [h1check dir]                    # gross vs cost, persistence, basis/squeeze risk, BY q
