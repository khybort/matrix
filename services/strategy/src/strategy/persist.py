"""Persist PredictionDrafts → predictions table."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta

from loguru import logger

from matrix_shared import session_scope
from matrix_shared.models import Prediction

from strategy.base import PredictionDraft


async def persist_drafts(drafts: Sequence[PredictionDraft]) -> int:
    if not drafts:
        return 0
    async with session_scope() as session:
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
                )
            )
    logger.info(f"persisted {len(drafts)} predictions")
    return len(drafts)
