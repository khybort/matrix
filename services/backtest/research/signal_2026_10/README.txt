Signal research 2026-10 — reproducible scripts (host python3 + pandas; bar.py in the backtest image).
Write-up: docs/wiki/signal-research-2026-10.md. Pre-registration with timestamps: PREREG.txt.

  mkdir data && curl linear instruments into data/linear_instr.json, tickers into data/linear_tickers.json
  (see fetch.py header), plus Bybit /v5/spot-margin-trade/data -> data/margin_data.json and
  Binance bapi margin vip/spec/list-all -> data/bn_margin.json (today's borrow rates).
  python3 fetch.py data ; python3 fetch_oi.py      # ~1.5 h, public endpoints only
  python3 research.py train   ; python3 research.py holdout H1
  docker run ... matrix-backtest:local python bar.py results/<split>.json   # BY + deflated Sharpe
