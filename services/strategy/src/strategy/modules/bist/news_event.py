"""BIST news-event reaction v1.

Hypothesis: a KAP/major-media headline mentioning a BIST ticker often
precedes a directional move within the session. We emit a long-biased signal
when:
  * a recent (within RECENT_WINDOW_S) RawDocument from a TR/KAP source
  * the document's title or body contains the bare ticker
  * and we are currently in BIST session

This is the BIST counterpart to the crypto `news_reaction` strategy; sentiment
scoring is intentionally left to the decision agent's news_score signal.
Long-only — under BIST rules the strategy doesn't emit shorts even if the
headline reads negative.

Horizon: 1 hour (session-aligned reaction window).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from loguru import logger
from sqlalchemy import desc, or_, select

from matrix_shared import session_scope
from matrix_shared.models import RawDocument

from strategy.base import PredictionDraft
from strategy.modules.bist._helpers import (
    BIST_EXCHANGE,
    active_bist_symbols,
    in_session,
    latest_bar,
)

STRATEGY_ID = "bist_news_event"
STRATEGY_VERSION = 1
RECENT_WINDOW_S = 3600  # only headlines from the last hour
TR_SOURCES = ("kap", "bloomberg_ht", "dunya", "hurriyet_ekonomi")
HORIZON_S = 3600


class BistNewsEvent:
    id: str = STRATEGY_ID
    version: int = STRATEGY_VERSION
    horizon_seconds: int = HORIZON_S
    market: str = "bist"
    asset_class: str = "bist"

    async def generate(self) -> list[PredictionDraft]:
        now = datetime.now(UTC)
        if not in_session(now):
            return []
        drafts: list[PredictionDraft] = []
        cutoff = now - timedelta(seconds=RECENT_WINDOW_S)

        async with session_scope() as session:
            symbols = await active_bist_symbols(session)

            doc_stmt = (
                select(RawDocument)
                .where(RawDocument.source.in_(TR_SOURCES))
                .where(
                    or_(
                        RawDocument.published_at >= cutoff,
                        RawDocument.created_at >= cutoff,
                    )
                )
                .order_by(desc(RawDocument.published_at))
                .limit(200)
            )
            docs = (await session.execute(doc_stmt)).scalars().all()
            if not docs:
                return []

            seen: set[str] = set()
            for sym in symbols:
                token = sym.upper()
                for doc in docs:
                    hay = f"{doc.title or ''} {doc.body or ''}".upper()
                    if token not in hay:
                        continue
                    # Avoid emitting many drafts per (symbol, doc); first match only.
                    key = f"{sym}:{doc.id}"
                    if key in seen:
                        continue
                    seen.add(key)

                    bar = await latest_bar(session, sym, interval="1m")
                    if bar is None:
                        break
                    last_px = Decimal(bar.close)

                    drafts.append(
                        PredictionDraft(
                            strategy_id=self.id,
                            strategy_version=self.version,
                            symbol=sym,
                            exchange=BIST_EXCHANGE,
                            asset_class=self.asset_class,
                            side="long",
                            confidence=Decimal("0.35"),  # event-driven, modest base
                            horizon_seconds=self.horizon_seconds,
                            entry_price_ref=last_px,
                            generated_at=now,
                            thesis=(
                                f"news mention [{doc.source}] '{(doc.title or '')[:80]}' "
                                f"contains {token}"
                            ),
                            context={
                                "doc_id": str(doc.id),
                                "source": doc.source,
                                "published_at": (
                                    doc.published_at.isoformat() if doc.published_at else None
                                ),
                                "long_only": True,
                            },
                        )
                    )
                    logger.info(
                        f"{self.id}: {sym} long news_src={doc.source} "
                        f"doc={str(doc.id)[:8]}"
                    )
                    break  # one signal per symbol per cycle

        return drafts
