"""Screener-follow v1 — convert OI/funding screener candidates into trades.

Hypothesis: when the deterministic screener marks a symbol as 'candidate'
(≥ CONFIRM_PASSES consecutive qualifying polls), the underlying force is
durable enough to trade against. The signal direction is derived from the
signal type + funding sign:

  funding_extreme:
    high positive funding → longs crowded → fade (SHORT)
    high negative funding → shorts crowded → fade (LONG)

  oi_spike:
    OI grew rapidly → squeeze brewing. Funding sign reveals which side
    is crowded and therefore more likely to unwind:
    funding_rate >= 0  → shorts getting squeezed → expect UP → LONG
    funding_rate < 0   → longs getting squeezed → expect DOWN → SHORT

  top_funding: informational only — SKIP (no trade signal).

Cooldown: if the same (symbol, signal_type) already produced a prediction
within the last horizon_s seconds, skip.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from loguru import logger
from sqlalchemy import desc, select, text

from matrix_shared import local_session_scope, shared_session_scope
from matrix_shared.models import MarketTrade, Prediction
from matrix_shared.markets.crypto import crypto_universe

from strategy.base import PredictionDraft

STRATEGY_ID = "screener_follow"
STRATEGY_VERSION = 1

# Was 3 (matching CONFIRM_PASSES in screener.py); dropped to 2 so candidates
# with two consecutive threshold crossings already start trading — 573 signal
# rows accumulated but 0 reached 3 passes within the 1h freshness window.
DEFAULT_MIN_PASSES = 2
DEFAULT_MIN_SCORE = Decimal("0.05")
DEFAULT_HORIZON_S = 1800        # 30 min — medium-term OI/funding signal
DEFAULT_CONFIDENCE = Decimal("0.55")
DEFAULT_TP_PCT = Decimal("0.020")  # 2% take-profit
DEFAULT_SL_PCT = Decimal("0.010")  # 1% stop-loss

_TRADABLE_SIGNAL_TYPES = frozenset({"funding_extreme", "oi_spike"})


class ScreenerFollow:
    id: str = STRATEGY_ID
    version: int = STRATEGY_VERSION
    market: str = "crypto"

    def __init__(
        self,
        *,
        symbols: Sequence[str] | None = None,
        min_passes: int = DEFAULT_MIN_PASSES,
        min_score: Decimal = DEFAULT_MIN_SCORE,
        horizon_s: int = DEFAULT_HORIZON_S,
        confidence: Decimal = DEFAULT_CONFIDENCE,
        tp_pct: Decimal | None = DEFAULT_TP_PCT,
        sl_pct: Decimal | None = DEFAULT_SL_PCT,
    ) -> None:
        self.symbols: list[str] = list(symbols) if symbols is not None else list(crypto_universe())
        self.min_passes = min_passes
        self.min_score = min_score
        self.horizon_seconds = horizon_s
        self.confidence = confidence
        self.tp_pct = Decimal(str(tp_pct)) if tp_pct is not None else None
        self.sl_pct = Decimal(str(sl_pct)) if sl_pct is not None else None

    async def generate(self) -> list[PredictionDraft]:
        now = datetime.now(UTC)
        fresh_cutoff = now - timedelta(hours=1)
        cooldown_cutoff = now - timedelta(seconds=self.horizon_seconds)
        drafts: list[PredictionDraft] = []

        # --- 1. Read qualifying screener_signals from LOCAL DB -----------------
        async with local_session_scope() as local:
            rows = (
                await local.execute(
                    text("""
                        SELECT symbol, signal_type, funding_rate, score, passes
                        FROM screener_signals
                        WHERE status = 'candidate'
                          AND passes >= :min_passes
                          AND score >= :min_score
                          AND observed_at > :fresh_cutoff
                        ORDER BY score DESC
                    """),
                    {
                        "min_passes": self.min_passes,
                        "min_score": float(self.min_score),
                        "fresh_cutoff": fresh_cutoff,
                    },
                )
            ).fetchall()

        # --- 2. Filter to tradable symbols + signal types ----------------------
        tradable_symbols = set(self.symbols)

        for row in rows:
            symbol: str = row.symbol
            signal_type: str = row.signal_type
            funding_rate: Decimal = Decimal(str(row.funding_rate)) if row.funding_rate is not None else Decimal("0")
            score: Decimal = Decimal(str(row.score))
            passes: int = int(row.passes)

            # Only act on tradable signal types and universe symbols.
            if signal_type not in _TRADABLE_SIGNAL_TYPES:
                continue
            if symbol not in tradable_symbols:
                continue

            # --- 3. Direction logic --------------------------------------------
            side: str
            if signal_type == "funding_extreme":
                # Very positive funding → longs crowded → fade (SHORT).
                # Very negative funding → shorts crowded → fade (LONG).
                side = "short" if funding_rate >= 0 else "long"
            elif signal_type == "oi_spike":
                # OI spike: which side is crowded reveals squeeze direction.
                # funding_rate >= 0 → shorts crowded → OI squeeze UP → LONG.
                # funding_rate < 0  → longs crowded → OI squeeze DOWN → SHORT.
                side = "long" if funding_rate >= 0 else "short"
            else:
                continue  # unreachable given _TRADABLE_SIGNAL_TYPES guard

            # --- 4. Cooldown: skip if (symbol, signal_type) fired recently -----
            # context is plain JSON so we check thesis which embeds signal_type.
            async with shared_session_scope() as shared:
                dup = (
                    await shared.execute(
                        select(Prediction.id)
                        .where(Prediction.strategy_id == STRATEGY_ID)
                        .where(Prediction.symbol == symbol)
                        .where(Prediction.thesis.contains(signal_type))
                        .where(Prediction.generated_at >= cooldown_cutoff)
                        .limit(1)
                    )
                ).first()
            if dup is not None:
                logger.debug(
                    f"{STRATEGY_ID}: cooldown active for {symbol}/{signal_type}"
                )
                continue

            # --- 5. Current price from latest MarketTrade in LOCAL --------------
            async with local_session_scope() as local:
                px_row = (
                    await local.execute(
                        select(MarketTrade.price, MarketTrade.exchange)
                        .where(MarketTrade.symbol == symbol)
                        .order_by(desc(MarketTrade.trade_ts))
                        .limit(1)
                    )
                ).first()
            if px_row is None:
                logger.debug(f"{STRATEGY_ID}: no recent trade for {symbol}; skipping")
                continue
            last_px = Decimal(str(px_row.price))
            exchange = px_row.exchange

            # --- 6. Emit draft --------------------------------------------------
            thesis = (
                f"screener {signal_type}: passes={passes} score={score:.4f} "
                f"funding={funding_rate:.4%}; expect {side}"
            )
            drafts.append(
                PredictionDraft(
                    strategy_id=self.id,
                    strategy_version=self.version,
                    symbol=symbol,
                    exchange=exchange,
                    side=side,
                    confidence=self.confidence,
                    horizon_seconds=self.horizon_seconds,
                    entry_price_ref=last_px,
                    generated_at=now,
                    thesis=thesis,
                    context={
                        "signal_type": signal_type,
                        "score": str(score),
                        "passes": passes,
                        "funding_rate": str(funding_rate),
                    },
                    tp_pct=self.tp_pct,
                    sl_pct=self.sl_pct,
                )
            )
            logger.info(
                f"{STRATEGY_ID}: signal {symbol}/{signal_type} → {side} "
                f"conf={self.confidence} score={score:.4f} funding={funding_rate:.4%}"
            )

        return drafts
