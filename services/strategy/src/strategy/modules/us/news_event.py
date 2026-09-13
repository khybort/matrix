"""US news-event reaction v1.

A fresh headline naming a US company often precedes a directional move. We emit
a modest long-biased signal when a recent RawDocument mentions a ticker — but
matching bare 1–5 letter US tickers in English prose is hopelessly noisy ("A",
"ON", "IT", "ALL" are all real tickers *and* common words). So we match the
company **name** (from `us_symbols`) or an explicit `$TICKER` cashtag instead.

Direction is intentionally left long-biased: the decision agent's `news_score`
owns sentiment. Horizon: 1 hour.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from loguru import logger
from sqlalchemy import desc, or_, select

from matrix_shared import session_scope
from matrix_shared.models import RawDocument, UsSymbol

from strategy.base import PredictionDraft
from strategy.modules.us._helpers import (
    US_EXCHANGE,
    in_session,
    latest_bar,
)

STRATEGY_ID = "us_news_event"
STRATEGY_VERSION = 1
RECENT_WINDOW_S = 3600  # only headlines from the last hour
# TR-only feeds are excluded; everything else (reuters/bloomberg/cnbc/…) counts.
EXCLUDED_SOURCES = ("kap", "bloomberg_ht", "dunya", "hurriyet_ekonomi")
HORIZON_S = 3600
DEFAULT_TP_PCT = Decimal("0.030")
DEFAULT_SL_PCT = Decimal("0.015")
# Names shorter than this are too generic to match safely on (e.g. "AT&T").
_MIN_NAME_LEN = 5


def _clean_name(name: str) -> str:
    """Strip common corporate suffixes so 'Apple Inc.' matches 'Apple'."""
    n = name.upper()
    for suffix in (" INC", " INC.", " CORP", " CORPORATION", " CO.", " CO",
                   " PLC", " LTD", " LLC", " COMPANY", " HOLDINGS", " GROUP", ","):
        n = n.replace(suffix, "")
    return n.strip()


class UsNewsEvent:
    id: str = STRATEGY_ID
    version: int = STRATEGY_VERSION
    horizon_seconds: int = HORIZON_S
    market: str = "us"
    asset_class: str = "us"

    def __init__(
        self,
        *,
        tp_pct: Decimal | None = DEFAULT_TP_PCT,
        sl_pct: Decimal | None = DEFAULT_SL_PCT,
    ) -> None:
        self.tp_pct = Decimal(str(tp_pct)) if tp_pct is not None else None
        self.sl_pct = Decimal(str(sl_pct)) if sl_pct is not None else None

    async def generate(self) -> list[PredictionDraft]:
        now = datetime.now(UTC)
        if not in_session(now):
            return []
        drafts: list[PredictionDraft] = []
        cutoff = now - timedelta(seconds=RECENT_WINDOW_S)

        async with session_scope() as session:
            sym_rows = (
                await session.execute(
                    select(UsSymbol.symbol, UsSymbol.name).where(UsSymbol.active.is_(True))
                )
            ).all()
            # symbol → (cashtag, cleaned_name|None)
            matchers: dict[str, tuple[str, str | None]] = {}
            for sym, name in sym_rows:
                cleaned = _clean_name(name) if name else None
                if cleaned is not None and len(cleaned) < _MIN_NAME_LEN:
                    cleaned = None
                matchers[sym] = (f"${sym.upper()}", cleaned)

            doc_stmt = (
                select(RawDocument)
                .where(RawDocument.source.notin_(EXCLUDED_SOURCES))
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
            for sym, (cashtag, cleaned) in matchers.items():
                for doc in docs:
                    hay = f"{doc.title or ''} {doc.body or ''}".upper()
                    hit = cashtag in hay or (cleaned is not None and cleaned in hay)
                    if not hit:
                        continue
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
                            exchange=US_EXCHANGE,
                            asset_class=self.asset_class,
                            side="long",
                            confidence=Decimal("0.35"),
                            horizon_seconds=self.horizon_seconds,
                            entry_price_ref=last_px,
                            generated_at=now,
                            thesis=(
                                f"news mention [{doc.source}] "
                                f"'{(doc.title or '')[:80]}' matches {sym}"
                            ),
                            context={
                                "doc_id": str(doc.id),
                                "source": doc.source,
                                "matched_on": "cashtag" if cashtag in hay else "name",
                                "published_at": (
                                    doc.published_at.isoformat() if doc.published_at else None
                                ),
                            },
                            tp_pct=self.tp_pct,
                            sl_pct=self.sl_pct,
                        )
                    )
                    logger.info(
                        f"{self.id}: {sym} long news_src={doc.source} doc={str(doc.id)[:8]}"
                    )
                    break  # one signal per symbol per cycle

        return drafts
