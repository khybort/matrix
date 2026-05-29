"""Cross-sectional momentum v1.

Hypothesis: relative strength across the crypto universe persists over a
multi-day horizon. Symbols that have outperformed their peers over the past
N days tend to continue outperforming; underperformers tend to continue
lagging. Long the top-K / short the bottom-K after excluding symbols whose
rolling volatility is too high to get a clean read on the signal.

Reference Sharpe ~1.0-1.2 with vol filter (empirical crypto cross-section
research, Briplotnik et al.).

Implementation:
    Pull the last `lookback_days * 24` 1h bars for each symbol.
    For each symbol:
      pct_return  = (close_last - close_first) / close_first
      std_pct     = std(hourly log-returns) over the window
      If std_pct > vol_filter_pct → skip (too noisy)
    Rank surviving symbols by pct_return descending:
      Top-K   → long  at DEFAULT_CONFIDENCE
      Bottom-K → short at DEFAULT_CONFIDENCE
    Cooldown: skip if a fresh prediction for the same symbol+strategy was
    emitted within the last `horizon_s` seconds (dedup via SHARED predictions).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from loguru import logger
from sqlalchemy import desc, select

from matrix_shared import session_scope, shared_session_scope
from matrix_shared.models import MarketBar, Prediction
from matrix_shared.markets.crypto import crypto_universe
from strategy.base import PredictionDraft

STRATEGY_ID = "momentum_xs"
STRATEGY_VERSION = 1
DEFAULT_LOOKBACK_DAYS = 7
DEFAULT_TOP_K = 3
DEFAULT_VOL_FILTER_PCT = "0.50"
DEFAULT_HORIZON_S = 3600
DEFAULT_CONFIDENCE = "0.60"

EXCHANGE_FALLBACK = "bybit"


class MomentumXs:
    id: str = STRATEGY_ID
    version: int = STRATEGY_VERSION
    horizon_seconds: int
    market: str = "crypto"

    def __init__(
        self,
        *,
        symbols: Sequence[str] | None = None,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
        top_k: int = DEFAULT_TOP_K,
        vol_filter_pct: Decimal = Decimal(DEFAULT_VOL_FILTER_PCT),
        horizon_s: int = DEFAULT_HORIZON_S,
    ) -> None:
        self.symbols = list(symbols) if symbols else list(crypto_universe())
        self.lookback_days = lookback_days
        self.top_k = top_k
        self.vol_filter_pct = Decimal(str(vol_filter_pct))
        self.horizon_seconds = horizon_s

    async def generate(self) -> list[PredictionDraft]:
        now = datetime.now(UTC)
        lookback_since = now - timedelta(days=self.lookback_days)
        dedup_since = now - timedelta(seconds=self.horizon_seconds)

        # ---- fetch bars per symbol ----
        # symbol → list[close] in ascending time order
        closes_by_sym: dict[str, list[Decimal]] = {}

        async with session_scope() as local:
            for symbol in self.symbols:
                stmt = (
                    select(MarketBar.close)
                    .where(MarketBar.symbol == symbol)
                    .where(MarketBar.asset_class == "crypto")
                    .where(MarketBar.interval == "1h")
                    .where(MarketBar.ts >= lookback_since)
                    .order_by(MarketBar.ts.asc())
                )
                rows = (await local.execute(stmt)).all()
                if len(rows) < 2:
                    continue
                closes_by_sym[symbol] = [Decimal(str(r.close)) for r in rows]

        if not closes_by_sym:
            return []

        # ---- compute metrics, apply vol filter ----
        # pct_return over the full lookback window (first close → last close)
        # std_pct = std of per-bar log-returns (proxy for daily vol)
        ranked: list[tuple[str, Decimal, Decimal, int]] = []  # (symbol, pct_return, std, n)

        for symbol, closes in closes_by_sym.items():
            close_first = closes[0]
            close_last = closes[-1]
            if close_first == 0:
                continue
            pct_return = (close_last - close_first) / close_first

            # hourly log-returns
            log_rets: list[float] = []
            for i in range(1, len(closes)):
                prev = float(closes[i - 1])
                curr = float(closes[i])
                if prev <= 0 or curr <= 0:
                    continue
                log_rets.append(math.log(curr / prev))

            if len(log_rets) < 2:
                continue

            n = len(log_rets)
            mean = sum(log_rets) / n
            variance = sum((r - mean) ** 2 for r in log_rets) / (n - 1)
            std_pct = Decimal(str(math.sqrt(variance)))

            if std_pct > self.vol_filter_pct:
                logger.debug(
                    f"{STRATEGY_ID}: {symbol} skipped — std_pct={std_pct:.4f} "
                    f"> vol_filter={self.vol_filter_pct}"
                )
                continue

            ranked.append((symbol, pct_return, std_pct, len(closes)))

        if not ranked:
            return []

        # Sort descending by pct_return
        ranked.sort(key=lambda x: x[1], reverse=True)
        n_surv = len(ranked)

        # ---- dedup: symbols already predicted recently ----
        async with shared_session_scope() as shared:
            dup_stmt = (
                select(Prediction.symbol)
                .where(Prediction.strategy_id == STRATEGY_ID)
                .where(Prediction.generated_at >= dedup_since)
            )
            recent_syms: set[str] = {
                r.symbol for r in (await shared.execute(dup_stmt)).all()
            }

        # ---- emit top-K long + bottom-K short ----
        confidence = Decimal(DEFAULT_CONFIDENCE)
        drafts: list[PredictionDraft] = []

        def _make_draft(
            symbol: str,
            pct_return: Decimal,
            std_pct: Decimal,
            rank: int,
            side: str,
        ) -> PredictionDraft:
            # rank is 1-indexed from the top (so rank=1 = highest return)
            display_rank = rank if side == "long" else (n_surv - rank + 1)
            return PredictionDraft(
                strategy_id=STRATEGY_ID,
                strategy_version=STRATEGY_VERSION,
                symbol=symbol,
                exchange=EXCHANGE_FALLBACK,
                side=side,
                confidence=confidence,
                horizon_seconds=self.horizon_seconds,
                entry_price_ref=closes_by_sym[symbol][-1],
                generated_at=now,
                thesis=(
                    f"7d return {pct_return:+.2%}, std {std_pct:.2%} — "
                    f"rank {rank} of {n_surv}, {side} momentum"
                ),
                context={
                    "lookback_days": self.lookback_days,
                    "pct_return": str(pct_return),
                    "std_pct": str(std_pct),
                    "rank": rank,
                    "n_survivors": n_surv,
                },
            )

        # Top-K → long
        for i, (symbol, pct_return, std_pct, _) in enumerate(ranked[: self.top_k], start=1):
            if symbol in recent_syms:
                logger.debug(f"{STRATEGY_ID}: {symbol} dedup skip (long)")
                continue
            draft = _make_draft(symbol, pct_return, std_pct, i, "long")
            drafts.append(draft)
            logger.info(
                f"{STRATEGY_ID}: signal {symbol} long rank={i}/{n_surv} "
                f"ret={pct_return:+.3%} std={std_pct:.3%} conf={confidence}"
            )

        # Bottom-K → short (reversed: highest index = lowest return)
        bottom = ranked[-self.top_k :]  # already sorted desc, so last = lowest
        for i, (symbol, pct_return, std_pct, _) in enumerate(
            reversed(bottom), start=1
        ):
            if symbol in recent_syms:
                logger.debug(f"{STRATEGY_ID}: {symbol} dedup skip (short)")
                continue
            rank_from_bottom = i  # 1 = lowest return
            draft = _make_draft(symbol, pct_return, std_pct, rank_from_bottom, "short")
            drafts.append(draft)
            logger.info(
                f"{STRATEGY_ID}: signal {symbol} short rank={rank_from_bottom}/{n_surv} "
                f"ret={pct_return:+.3%} std={std_pct:.3%} conf={confidence}"
            )

        return drafts
