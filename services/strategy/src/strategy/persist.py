"""Persist PredictionDrafts → predictions table."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import select, text

from matrix_shared import shared_session_scope
from matrix_shared.models import Prediction

from strategy.base import PredictionDraft


# Longest horizon any module emits is the 48 h carry family; a week bounds the
# lookup to the (symbol, generated_at) index without missing a live bet.
_EPISODE_LOOKBACK = timedelta(days=7)


def _bet_key(d: PredictionDraft) -> tuple[str, str, str, str, bool]:
    return (
        d.strategy_id,
        d.asset_class,
        d.symbol,
        d.side,
        bool((d.context or {}).get("is_shadow", False)),
    )


def drop_reemissions(
    drafts: Sequence[PredictionDraft], live: set[tuple[str, str, str, str, bool]]
) -> list[PredictionDraft]:
    """Keep a draft only when the same bet — (strategy, market, symbol, side,
    champion|shadow) — is not already inside the horizon of an earlier
    prediction, and only once per batch.

    A module re-evaluates its condition every tick and emits again while the
    condition holds. Measured 2026-10-09 over 30 days: 83 % of momentum_xs
    rows, 75 % of oi_delta, 76 % of oi_breakout, 91 % of funding_reversion and
    ~50 % of the BIST breakout/reversion rows were re-emissions of a bet
    already open. The wallet can hold each bet once, so those rows are not
    evidence: they multiplied one afternoon's luck into momentum_xs's "+36 bps,
    t=6" and flooded the candidate pool with copies of the same idea.
    """
    kept: list[PredictionDraft] = []
    seen = set(live)
    for d in drafts:
        key = _bet_key(d)
        if key in seen:
            continue
        seen.add(key)
        kept.append(d)
    return kept


async def _live_bets(drafts: Sequence[PredictionDraft]) -> set[tuple[str, str, str, str, bool]]:
    """Bets whose earlier prediction is still inside its horizon, whatever its
    status (an unfilled, expired-as-stale signal still owns its horizon)."""
    now = datetime.now(UTC)
    async with shared_session_scope() as session:
        rows = (
            await session.execute(
                select(
                    Prediction.strategy_id,
                    Prediction.asset_class,
                    Prediction.symbol,
                    Prediction.side,
                    text("coalesce(predictions.context->>'is_shadow', 'false') = 'true'"),
                )
                .where(Prediction.symbol.in_({d.symbol for d in drafts}))
                .where(Prediction.generated_at > now - _EPISODE_LOOKBACK)
                .where(Prediction.close_by > now)
                .where(Prediction.strategy_id.in_({d.strategy_id for d in drafts}))
            )
        ).all()
    return {(sid, ac, sym, side, bool(sh)) for sid, ac, sym, side, sh in rows}


async def apply_episode_guard(drafts: Sequence[PredictionDraft]) -> list[PredictionDraft]:
    if not drafts:
        return []
    kept = drop_reemissions(drafts, await _live_bets(drafts))
    if len(kept) < len(drafts):
        logger.debug(f"episode guard: dropped {len(drafts) - len(kept)} re-emission(s)")
    return kept


async def apply_backpressure(drafts: Sequence[PredictionDraft]) -> list[PredictionDraft]:
    """Keep, per (strategy, market, champion|shadow), only the highest-confidence
    drafts that fit the strategy's open-backlog room (matrix_shared.backpressure).
    funding_reversion alone was persisting ~9k/day predictions for 1 slot."""
    from matrix_shared.backpressure import room

    groups: dict[tuple[str, str, bool], list[PredictionDraft]] = {}
    for d in drafts:
        shadow = bool((d.context or {}).get("is_shadow", False))
        groups.setdefault((d.strategy_id, d.asset_class, shadow), []).append(d)
    kept: list[PredictionDraft] = []
    for (sid, ac, shadow), ds in groups.items():
        r = await room(sid, ac, shadow=shadow)
        if r >= len(ds):
            kept.extend(ds)
            continue
        ds_sorted = sorted(ds, key=lambda d: d.confidence, reverse=True)
        kept.extend(ds_sorted[:r])
        logger.info(
            f"backpressure: {sid}/{ac}{' shadow' if shadow else ''} backlog full — "
            f"kept {r}/{len(ds)} drafts (highest confidence)"
        )
    return kept


async def persist_drafts(drafts: Sequence[PredictionDraft]) -> int:
    if not drafts:
        return 0
    drafts = await apply_episode_guard(drafts)
    if not drafts:
        return 0
    drafts = await apply_backpressure(drafts)
    if not drafts:
        return 0
    # Predictions live in the SHARED tier — backtest/reflection/wallet all
    # read from there. matrix-agent already does this; strategy modules
    # were inadvertently writing to LOCAL.
    async with shared_session_scope() as session:
        for d in drafts:
            session.add(
                Prediction(
                    id=d.id,
                    strategy_id=d.strategy_id,
                    strategy_version=d.strategy_version,
                    generated_at=d.generated_at,
                    symbol=d.symbol,
                    exchange=d.exchange,
                    asset_class=d.asset_class,
                    side=d.side,
                    confidence=d.confidence,
                    horizon_seconds=d.horizon_seconds,
                    close_by=d.generated_at + timedelta(seconds=d.horizon_seconds),
                    entry_price_ref=d.entry_price_ref,
                    thesis=d.thesis,
                    context=d.context,
                    status="open",
                    tp_pct=d.tp_pct,
                    sl_pct=d.sl_pct,
                )
            )
    logger.info(f"persisted {len(drafts)} predictions")
    return len(drafts)
