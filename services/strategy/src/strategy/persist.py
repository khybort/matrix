"""Persist PredictionDrafts → predictions table."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta

from loguru import logger

from matrix_shared import shared_session_scope
from matrix_shared.models import Prediction

from strategy.base import PredictionDraft


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
