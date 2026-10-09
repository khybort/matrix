"""Front-end IV inversion (iv_inversion) v1 — shadow leg of the r5f forward test.

Round 5 (docs/wiki/signal-research-2026-10.md, "Round 5: options-implied"),
cell D.3: when the front-month ATM implied vol of BTC or ETH options trades far
above the back month (TERM at or above its trailing-365-day 90th percentile,
acute stress), the perp rose over the next 3 days. Positive in both splits
(+136 / +115 bps per episode, medians +166 / +201, ~21 episodes a year) but
train t 0.77, so it failed the gate. It is now forward-tested at n = 60
(r5f.D.3, services/backtest/research/signal_2026_10_r5f/PREREG.md). The
decision is the harness evaluation at n = 60; this module trades the same
signals on the shadow wallet so the shadow tracker shows how the live fills
compare with the band (strategy_configs.params.shadow_band).

The rule is `matrix_shared.iv_term` (shared with the recorder and the r5f
builder), over `deribit_iv_daily` rows written by `ingestion.iv_term_recorder`
just after 00:00 UTC. On a decision day D the module emits one long per asset
whose signal window starts on D and that has no episode open, from D + 1h
(round 5 entered at the close of the bar ending D + 1h) until D + 1h +
`max_entry_delay_s`; later it logs the entry as missed. Horizon 72 h, no
take-profit or stop-loss (round 5 used none). Re-emissions inside the
window are dropped by the dispatcher's episode guard. One decision line per
asset per day is logged, whether or not it fires.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from loguru import logger
from sqlalchemy import text

from matrix_shared import iv_term, local_session_scope

from strategy.base import PredictionDraft

STRATEGY_ID = "iv_inversion"
STRATEGY_VERSION = 1
DEFAULT_HORIZON_S = int(iv_term.HOLD_H * 3600)
DEFAULT_MAX_ENTRY_DELAY_S = 6 * 3600
_HISTORY_DAYS = iv_term.LOOKBACK_DAYS + 30  # percentile history + room to replay open windows

# (asset, day) -> last logged state, so the 30 s loop logs each decision once
_logged: dict[tuple[str, date], str] = {}


async def load_history(currency: str, today: date) -> dict[int, float | None]:
    """day ms -> TERM for the asset's rows in the last _HISTORY_DAYS (None = missing by the rule)."""
    async with local_session_scope() as s:
        rows = (await s.execute(
            text("SELECT day, term FROM deribit_iv_daily WHERE currency = :c AND day > :since AND day <= :today"),
            {"c": currency, "since": today - timedelta(days=_HISTORY_DAYS), "today": today},
        )).all()
    return {iv_term.to_ms(datetime(d.year, d.month, d.day, tzinfo=UTC)): t for d, t in rows}


def _log_once(asset: str, day: date, msg: str) -> None:
    """Log a decision line once per change (the inputs can change while the recorder backfills)."""
    if _logged.get((asset, day)) == msg:
        return
    _logged[(asset, day)] = msg
    for k in [k for k in _logged if k[1] < day - timedelta(days=3)]:
        del _logged[k]
    logger.info(msg)


class IvInversion:
    id: str = STRATEGY_ID
    version: int = STRATEGY_VERSION
    market: str = "crypto"

    def __init__(
        self,
        symbols: Sequence[str] | None = None,  # fixed BTC/ETH; the dispatcher's universe is ignored
        *,
        threshold: float = iv_term.THRESHOLD,
        horizon_s: int = DEFAULT_HORIZON_S,
        max_entry_delay_s: int = DEFAULT_MAX_ENTRY_DELAY_S,
    ) -> None:
        self.symbols = list(iv_term.CURRENCIES.values())
        self.threshold = threshold
        self.horizon_s = horizon_s
        self.max_entry_delay_s = max_entry_delay_s

    def decide(self, history: dict[int, float | None], now: datetime) -> tuple[str, dict | None]:
        """(state, today's decision row). state: no_row | no_signal | wait | enter | missed."""
        day = iv_term.day_floor(now)
        day_ms = iv_term.to_ms(day)
        if day_ms not in history:
            return "no_row", None
        rows = iv_term.decisions(history, day_ms, day_ms, threshold=self.threshold,
                                 hold_h=self.horizon_s / 3600)
        d = rows[-1] if rows else None
        if d is None or not d["enter"]:
            return "no_signal", d
        since_entry = (now - day).total_seconds() - iv_term.ENTRY_LAG_H * 3600
        if since_entry < 0:
            return "wait", d
        if since_entry > self.max_entry_delay_s:
            return "missed", d
        return "enter", d

    async def generate(self) -> list[PredictionDraft]:
        now = datetime.now(UTC)
        today = now.date()
        drafts: list[PredictionDraft] = []
        for cur, sym in iv_term.CURRENCIES.items():
            history = await load_history(cur, today)
            state, d = self.decide(history, now)
            term = f"{d['term']:+.2f}" if d and d["term"] is not None else "missing"
            pct = f"{d['pct']:.3f}" if d and d["pct"] is not None else "n/a"
            if state == "no_row":
                _log_once(cur, today, f"{STRATEGY_ID}: {cur} {today}: no deribit_iv_daily row yet")
                continue
            if state != "enter":
                why = {
                    "no_signal": ("TERM missing or < 120 values in the last 365 d" if d and d["pct"] is None
                                  else f"pct {pct} < {self.threshold:.2f}" if not (d and d["on"])
                                  else "window already running or an episode is open"),
                    "wait": "fires; entry at 01:00 UTC",
                    "missed": f"fired but the entry window (D+1h .. +{self.max_entry_delay_s / 3600:.0f}h) passed",
                }[state]
                _log_once(cur, today,
                          f"{STRATEGY_ID}: {cur} {today}: TERM {term} pct {pct} -> {state} ({why})")
                continue
            async with local_session_scope() as s:
                px = (await s.execute(
                    text(
                        "SELECT coalesce(mark_price, last_price) FROM market_ticker_snapshots "
                        "WHERE symbol = :sym AND exchange = 'bybit' AND snapshot_ts > :since "
                        "ORDER BY snapshot_ts DESC LIMIT 1"
                    ),
                    {"sym": sym, "since": now - timedelta(minutes=5)},
                )).scalar()
            if px is None:
                _log_once(cur, today, f"{STRATEGY_ID}: {cur} {today}: fires but no fresh {sym} price")
                continue
            _log_once(cur, today, f"{STRATEGY_ID}: {cur} {today}: TERM {term} pct {pct} -> LONG {sym} 72h")
            drafts.append(
                PredictionDraft(
                    strategy_id=self.id,
                    strategy_version=self.version,
                    symbol=sym,
                    exchange="bybit",
                    side="long",
                    confidence=Decimal("0.5"),
                    horizon_seconds=self.horizon_s,
                    entry_price_ref=Decimal(str(px)),
                    generated_at=now,
                    thesis=(
                        f"{cur} front-end IV inversion: TERM {term} vol pts at pct {pct} of its trailing 365 d "
                        f"(>= {self.threshold:.2f}) on {today}; long the perp 72 h (round 5 D.3, forward test r5f)"
                    ),
                    context={
                        "rule": "r5f.D.3",
                        "decision_day": today.isoformat(),
                        "term": d["term"],
                        "pct_term": d["pct"],
                        "threshold": self.threshold,
                    },
                )
            )
        return drafts
