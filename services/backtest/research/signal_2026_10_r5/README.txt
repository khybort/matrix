Round 5 — options-implied signals on BTC/ETH perps. Write-up: docs/wiki/signal-research-2026-10.md ("Round 5: options-implied").
Pre-registration with timestamped log: PREREG.txt (committed alone in 1b2b6e1). Host python3 + pandas/numpy, public endpoints only.

  python3 fetch.py r5/data dvol       # Deribit DVOL 1h, BTC + ETH, 2021-03-24..
  python3 fetch.py r5/data bybit      # Bybit BTCUSDT/ETHUSDT 1h klines + funding history
  python3 fetch.py r5/data books      # 5 Bybit book snapshots per perp, 61 s apart (spread at $5k)
  python3 fetch.py r5/data trades     # Deribit option trades 20:00-24:00 UTC daily (history.deribit.com), ~2 h
  python3 research.py r5/data features
  python3 research.py r5/data train     # decision split; writes train_pass.json
  python3 research.py r5/data holdout   # train passers only, once
  python3 research.py r5/data record    # every cell, both splits (record only)
  python3 report.py r5/data             # descriptive extras for the write-up
