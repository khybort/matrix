Round 3b — H1 with a perp-perp hedge. Write-up: docs/wiki/signal-research-2026-10.md ("Round 3b").
Pre-registration with timestamped log: PREREG.txt. Host python3 + pandas/numpy, public endpoints only.

  mkdir -p r3b/data; fetch into r3b/data: fapi exchangeInfo -> bn_info.json, OKX public/instruments SWAP ->
  okx_info.json, Bitget v2 mix contracts usdt-futures -> bg_info.json, Gate futures/usdt/contracts -> gate_info.json
  python3 fetch_hedge.py r3b/data <round1 data dir>         # ~1 h; funding + 1h klines per hedge venue
  python3 research.py r3b/data <round1 data dir> episodes   # entry/exit/venue only, writes book_pairs.json
  python3 fetch_books.py r3b/data                           # today's depth (Binance: keep under its weight limit)
  python3 research.py r3b/data <round1 data dir> train      # decision split
  python3 research.py r3b/data <round1 data dir> record     # both splits, descriptive extras
