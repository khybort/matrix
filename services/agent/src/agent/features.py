"""Feature extraction — pulls recent state from DB for the decision agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import desc, select

from graph.queries import GraphAssetContext, base_token_to_asset, get_asset_context
from graph.read import get_remote_graph_signal, merge_contexts
from matrix_shared import session_scope
from matrix_shared.models import (
    MarketTrade,
    OrderBookSnapshot,
    RawDocument,
    TickerSnapshot,
)


@dataclass(slots=True)
class SymbolFeatures:
    """Snapshot of features for a single symbol at the time of the agent tick."""

    symbol: str
    last_price: Decimal | None = None

    # Trade flow (60s window)
    n_trades_60s: int = 0
    buy_share_60s: Decimal = Decimal("0.5")  # 0..1
    notional_60s_usd: Decimal = Decimal("0")

    # Orderbook microstructure (latest snapshot)
    spread_bps: Decimal | None = None  # (ask-bid)/mid * 10000
    bid_ask_imbalance_top5: Decimal | None = None  # bids_sz/(bids_sz+asks_sz), 0..1

    # Ticker (latest)
    funding_rate: Decimal | None = None
    open_interest: Decimal | None = None
    oi_delta_pct_5m: Decimal | None = None  # change over last 5min
    price_change_pct_5m: Decimal | None = None  # last_price vs ~5min ago

    # News (last 1h count + simple keyword-based polarity hint)
    n_news_1h: int = 0
    news_titles_sample: list[str] = field(default_factory=list)

    # Graph-derived (24h window over context graph)
    graph_mention_count: int = 0
    graph_recency_weight: Decimal = Decimal("0")
    graph_direct_polarity: Decimal = Decimal("0")
    graph_contextual_polarity: Decimal = Decimal("0")
    graph_related_companies: list[str] = field(default_factory=list)
    graph_co_mentioned_assets: list[str] = field(default_factory=list)


async def extract_symbol_features(symbol: str) -> SymbolFeatures:
    f = SymbolFeatures(symbol=symbol)
    now = datetime.now(UTC)

    async with session_scope() as session:
        # Trade flow over last 60s
        since = now - timedelta(seconds=60)
        stmt = select(MarketTrade.side, MarketTrade.size, MarketTrade.price).where(
            MarketTrade.symbol == symbol, MarketTrade.trade_ts >= since
        )
        trades = (await session.execute(stmt)).all()
        if trades:
            buy_vol = sum((Decimal(r.size) for r in trades if r.side == "buy"), Decimal(0))
            sell_vol = sum((Decimal(r.size) for r in trades if r.side == "sell"), Decimal(0))
            total = buy_vol + sell_vol
            if total > 0:
                f.buy_share_60s = buy_vol / total
            f.n_trades_60s = len(trades)
            last_px = Decimal(trades[-1].price)
            f.last_price = last_px
            f.notional_60s_usd = total * last_px

        # Latest orderbook snapshot
        ob_stmt = (
            select(OrderBookSnapshot)
            .where(OrderBookSnapshot.symbol == symbol)
            .order_by(desc(OrderBookSnapshot.snapshot_ts))
            .limit(1)
        )
        ob = (await session.execute(ob_stmt)).scalar_one_or_none()
        if ob is not None and ob.bids and ob.asks:
            try:
                best_bid = Decimal(str(ob.bids[0][0]))
                best_ask = Decimal(str(ob.asks[0][0]))
                mid = (best_bid + best_ask) / Decimal("2")
                if mid > 0:
                    f.spread_bps = (best_ask - best_bid) / mid * Decimal("10000")
                bids_sz = sum(Decimal(str(b[1])) for b in ob.bids[:5])
                asks_sz = sum(Decimal(str(a[1])) for a in ob.asks[:5])
                tot = bids_sz + asks_sz
                if tot > 0:
                    f.bid_ask_imbalance_top5 = bids_sz / tot
            except (ValueError, IndexError, ArithmeticError):
                pass

        # Latest ticker + OI delta vs 5min ago
        tk_stmt = (
            select(TickerSnapshot)
            .where(TickerSnapshot.symbol == symbol)
            .order_by(desc(TickerSnapshot.snapshot_ts))
            .limit(1)
        )
        tk = (await session.execute(tk_stmt)).scalar_one_or_none()
        if tk is not None:
            f.funding_rate = tk.funding_rate
            f.open_interest = tk.open_interest
            if tk.last_price is not None:
                f.last_price = tk.last_price
            if tk.open_interest:
                older = now - timedelta(minutes=5)
                older_stmt = (
                    select(TickerSnapshot.open_interest, TickerSnapshot.last_price)
                    .where(TickerSnapshot.symbol == symbol)
                    .where(TickerSnapshot.snapshot_ts <= older)
                    .order_by(desc(TickerSnapshot.snapshot_ts))
                    .limit(1)
                )
                old_row = (await session.execute(older_stmt)).first()
                if old_row:
                    if old_row[0]:
                        old_oi = Decimal(old_row[0])
                        if old_oi > 0 and tk.open_interest:
                            f.oi_delta_pct_5m = (tk.open_interest - old_oi) / old_oi
                    if old_row[1] and tk.last_price:
                        old_px = Decimal(old_row[1])
                        if old_px > 0:
                            f.price_change_pct_5m = (tk.last_price - old_px) / old_px

        # Recent news (1h window) — count + sample of titles. Filtered loosely
        # for symbol relevance: includes title containing the symbol's base
        # token (e.g. BTC for BTCUSDT) or generic "crypto"/"bitcoin"/"ethereum".
        base = _base_token(symbol)
        keywords = [base.lower()]
        if base == "BTC":
            keywords += ["bitcoin"]
        elif base == "ETH":
            keywords += ["ethereum"]
        news_since = now - timedelta(hours=1)
        news_stmt = (
            select(RawDocument.title)
            .where(RawDocument.published_at >= news_since)
            .order_by(desc(RawDocument.published_at))
            .limit(50)
        )
        news_rows = (await session.execute(news_stmt)).all()
        relevant = [
            r.title
            for r in news_rows
            if r.title and any(k in r.title.lower() for k in keywords)
        ]
        f.n_news_1h = len(relevant)
        f.news_titles_sample = relevant[:5]

    # Graph-derived features.
    # Federated read: local AGE first, then supplement with the freshest
    # remote aggregate any PC published into graph_signals. Local data
    # always wins when both are non-empty; remote fills coverage gaps.
    try:
        asset = base_token_to_asset(symbol)
        local_ctx: GraphAssetContext = await get_asset_context(asset, window_hours=24.0)
        ctx = local_ctx
        # Only consult remote if local is thin — saves a Neon round-trip when
        # we already have rich coverage.
        if (
            local_ctx.direct_mention_count < 3
            or len(local_ctx.related_companies) < 2
        ):
            remote_ctx = await get_remote_graph_signal(asset, max_age_minutes=30.0)
            if remote_ctx is not None:
                ctx = merge_contexts(local_ctx, remote_ctx)
        f.graph_mention_count = ctx.direct_mention_count
        f.graph_recency_weight = ctx.recency_weighted_count
        f.graph_direct_polarity = ctx.direct_polarity
        f.graph_contextual_polarity = ctx.contextual_polarity
        f.graph_related_companies = [c for c, _ in ctx.related_companies]
        f.graph_co_mentioned_assets = [a for a, _ in ctx.co_mentioned_assets]
    except Exception:
        # graph queries should never break feature extraction; fall back silently
        pass

    return f


def _base_token(symbol: str) -> str:
    for q in ("USDT", "USDC", "USD", "BUSD"):
        if symbol.endswith(q):
            return symbol[: -len(q)]
    return symbol
