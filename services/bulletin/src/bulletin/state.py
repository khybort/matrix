"""Aggregate engine state for the bulletin's LLM prompt.

Stays in the bulletin module rather than reusing notify.state because the
bulletin's view is wider (7-30 day windows, top-N rankings) and we want
the prompt context shape to be stable here independent of the bot's
status-screen needs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import case, desc, func, select

from matrix_shared import shared_session_scope
from matrix_shared.models import (
    GraphSignal,
    MutationProposal,
    Outcome,
    PaperTradeCertificate,
    Prediction,
    StrategyConfig,
    Wallet,
)


@dataclass(slots=True)
class BulletinSnapshot:
    """Bundle of facts fed to the LLM. JSON-friendly types throughout."""

    window_days: int
    issue_date: str  # YYYY-MM-DD UTC
    starting_capital_usd: str
    equity_usd: str
    equity_delta_pct: str
    n_trades_window: int
    n_wins_window: int
    win_rate_window: str
    total_pnl_usd_window: str
    by_strategy: list[dict[str, Any]] = field(default_factory=list)
    active_strategies: list[dict[str, Any]] = field(default_factory=list)
    new_mutations: list[dict[str, Any]] = field(default_factory=list)
    cert_changes: list[dict[str, Any]] = field(default_factory=list)
    asset_signals: list[dict[str, Any]] = field(default_factory=list)


async def collect_snapshot(window_days: int = 7) -> BulletinSnapshot:
    """One DB pass to gather everything the bulletin prompt needs."""
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=window_days)
    issue_date = now.strftime("%Y-%m-%d")

    async with shared_session_scope() as session:
        # --- wallet ---------------------------------------------------------
        wallet = (
            await session.execute(select(Wallet).order_by(Wallet.created_at).limit(1))
        ).scalar_one_or_none()
        starting = Decimal(wallet.starting_capital_usd) if wallet else Decimal("10000")
        equity = (
            Decimal(wallet.cash_usd) + Decimal(wallet.locked_usd)
            if wallet
            else Decimal("0")
        )
        delta_pct = (
            ((equity - starting) / starting * Decimal(100))
            if starting > 0
            else Decimal("0")
        )

        # --- 7d aggregate -----------------------------------------------------
        agg = (
            await session.execute(
                select(
                    func.count(Outcome.id),
                    func.sum(Outcome.pnl_usd),
                    func.sum(case((Outcome.pnl_usd > 0, 1), else_=0)),
                ).where(Outcome.observed_at >= since)
            )
        ).one()
        n, total_pnl, wins = agg
        n = int(n or 0)
        wins = int(wins or 0)
        total_pnl = Decimal(total_pnl or 0)
        win_rate = (Decimal(wins) / Decimal(n)) if n > 0 else Decimal("0")

        # --- per-strategy in window ------------------------------------------
        rows = (
            await session.execute(
                select(
                    Prediction.strategy_id,
                    func.count(Outcome.id).label("n"),
                    func.sum(Outcome.pnl_usd).label("pnl"),
                    func.sum(case((Outcome.pnl_usd > 0, 1), else_=0)).label("wins"),
                )
                .join(Outcome, Outcome.prediction_id == Prediction.id)
                .where(Outcome.observed_at >= since)
                .group_by(Prediction.strategy_id)
                .order_by(desc("n"))
            )
        ).all()
        by_strategy = [
            {
                "strategy_id": r.strategy_id,
                "n_trades": int(r.n),
                "total_pnl_usd": str(Decimal(r.pnl or 0).quantize(Decimal("0.01"))),
                "win_rate": (
                    str((Decimal(int(r.wins)) / Decimal(int(r.n))).quantize(Decimal("0.0001")))
                    if int(r.n)
                    else "0"
                ),
            }
            for r in rows
        ]

        # --- active strategies + cert state ---------------------------------
        cfgs = (
            await session.execute(
                select(StrategyConfig).where(StrategyConfig.status == "active")
            )
        ).scalars().all()
        active_strategies: list[dict[str, Any]] = []
        cert_changes: list[dict[str, Any]] = []
        for cfg in cfgs:
            cert = (
                await session.execute(
                    select(PaperTradeCertificate)
                    .where(PaperTradeCertificate.strategy_id == cfg.strategy_id)
                    .where(PaperTradeCertificate.asset_class == cfg.asset_class)
                    .where(PaperTradeCertificate.version == cfg.version)
                    .limit(1)
                )
            ).scalar_one_or_none()
            state = "no_cert"
            if cert is not None:
                if cert.status == "granted" and (
                    cert.validity_until is None or cert.validity_until > now
                ):
                    state = "valid"
                else:
                    state = cert.status
                if cert.granted_at and cert.granted_at >= since:
                    cert_changes.append({
                        "strategy_id": cfg.strategy_id,
                        "version": cfg.version,
                        "asset_class": cfg.asset_class,
                        "event": "granted",
                        "at": cert.granted_at.isoformat(),
                    })
            active_strategies.append({
                "strategy_id": cfg.strategy_id,
                "asset_class": cfg.asset_class,
                "version": cfg.version,
                "cert": state,
            })

        # --- mutations in window ---------------------------------------------
        muts = (
            await session.execute(
                select(MutationProposal)
                .where(MutationProposal.created_at >= since)
                .order_by(desc(MutationProposal.created_at))
                .limit(20)
            )
        ).scalars().all()
        new_mutations = [
            {
                "strategy_id": m.strategy_id,
                "from_v": m.from_version,
                "to_v": m.to_version,
                "type": m.proposal_type,
                "source": m.source,
                "status": m.status,
                "at": m.created_at.isoformat(),
            }
            for m in muts
        ]

        # --- asset graph signals (latest per asset) --------------------------
        graph_rows = (
            await session.execute(
                select(
                    GraphSignal.asset,
                    GraphSignal.direct_mention_count,
                    GraphSignal.direct_polarity,
                    GraphSignal.contextual_polarity,
                    GraphSignal.computed_at,
                )
                .where(GraphSignal.computed_at >= since)
                .order_by(GraphSignal.asset, desc(GraphSignal.computed_at))
                .distinct(GraphSignal.asset)
            )
        ).all()
        asset_signals = [
            {
                "asset": r.asset,
                "mentions": int(r.direct_mention_count),
                "direct_polarity": str(r.direct_polarity),
                "contextual_polarity": str(r.contextual_polarity),
            }
            for r in graph_rows
        ]

    return BulletinSnapshot(
        window_days=window_days,
        issue_date=issue_date,
        starting_capital_usd=str(starting.quantize(Decimal("0.01"))),
        equity_usd=str(equity.quantize(Decimal("0.01"))),
        equity_delta_pct=str(delta_pct.quantize(Decimal("0.0001"))),
        n_trades_window=n,
        n_wins_window=wins,
        win_rate_window=str(win_rate.quantize(Decimal("0.0001"))),
        total_pnl_usd_window=str(total_pnl.quantize(Decimal("0.01"))),
        by_strategy=by_strategy,
        active_strategies=active_strategies,
        new_mutations=new_mutations,
        cert_changes=cert_changes,
        asset_signals=asset_signals,
    )
