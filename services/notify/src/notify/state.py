"""Read-only snapshots of system state for the bot to format and push.

Every function here returns a plain dict — easy to JSON-serialize for
debugging, easy to feed into the message formatter. No mutation. The bot
process must NEVER call execution or trading paths; this is observability
glue.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import case, desc, func, select

from matrix_shared import shared_session_scope
from matrix_shared.models import (
    Outcome,
    PaperPosition,
    PaperTradeCertificate,
    Prediction,
    StrategyConfig,
    Wallet,
)


@dataclass(slots=True)
class WalletSnapshot:
    wallet_id: str
    name: str
    starting_capital_usd: Decimal
    cash_usd: Decimal
    locked_usd: Decimal
    equity_usd: Decimal
    net_pnl_usd: Decimal
    net_pnl_pct: Decimal
    circuit_tripped_at: datetime | None
    max_position_pct: Decimal
    max_concurrent_positions: int
    daily_loss_circuit_pct: Decimal


async def get_default_wallet() -> WalletSnapshot | None:
    async with shared_session_scope() as session:
        row = (
            await session.execute(select(Wallet).order_by(Wallet.created_at).limit(1))
        ).scalar_one_or_none()
        if row is None:
            return None
        cash = Decimal(row.cash_usd)
        locked = Decimal(row.locked_usd)
        starting = Decimal(row.starting_capital_usd)
        equity = cash + locked
        net = equity - starting
        return WalletSnapshot(
            wallet_id=str(row.id),
            name=row.name,
            starting_capital_usd=starting,
            cash_usd=cash,
            locked_usd=locked,
            equity_usd=equity,
            net_pnl_usd=net,
            net_pnl_pct=(net / starting * Decimal(100)) if starting > 0 else Decimal(0),
            circuit_tripped_at=row.circuit_tripped_at,
            max_position_pct=Decimal(row.max_position_pct),
            max_concurrent_positions=int(row.max_concurrent_positions),
            daily_loss_circuit_pct=Decimal(row.daily_loss_circuit_pct),
        )


async def get_open_positions_summary(wallet_id: str) -> dict[str, Any]:
    """Count + per-strategy breakdown of currently-open paper positions."""
    async with shared_session_scope() as session:
        stmt = (
            select(
                Prediction.strategy_id,
                func.count(PaperPosition.id).label("n"),
                func.sum(PaperPosition.notional_usd).label("notional"),
            )
            .join(Prediction, Prediction.id == PaperPosition.prediction_id)
            .where(PaperPosition.status == "open")
            .group_by(Prediction.strategy_id)
        )
        rows = (await session.execute(stmt)).all()
    by_strategy = {
        r.strategy_id: {"n": int(r.n), "notional_usd": str(Decimal(r.notional))}
        for r in rows
    }
    total_n = sum(v["n"] for v in by_strategy.values())
    return {"total": total_n, "by_strategy": by_strategy}


async def get_recent_pnl(window_hours: int = 24) -> dict[str, Any]:
    """Sum + count of outcomes in the last N hours."""
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    async with shared_session_scope() as session:
        agg = (
            await session.execute(
                select(
                    func.count(Outcome.id),
                    func.sum(Outcome.pnl_usd),
                    func.sum(case((Outcome.pnl_usd > 0, 1), else_=0)),
                )
                .where(Outcome.observed_at >= cutoff)
            )
        ).one()
    n, total_pnl, wins = agg
    n = int(n or 0)
    total = Decimal(total_pnl or 0)
    wins_n = int(wins or 0)
    return {
        "window_hours": window_hours,
        "n_outcomes": n,
        "total_pnl_usd": str(total),
        "win_rate": (wins_n / n) if n > 0 else None,
        "wins": wins_n,
        "losses": n - wins_n,
    }


async def get_active_strategies() -> list[dict[str, Any]]:
    """List of active strategy_configs with cert status."""
    async with shared_session_scope() as session:
        stmt = (
            select(StrategyConfig)
            .where(StrategyConfig.status == "active")
            .order_by(StrategyConfig.strategy_id, StrategyConfig.version)
        )
        configs = list((await session.execute(stmt)).scalars())

        out: list[dict[str, Any]] = []
        for cfg in configs:
            cert_stmt = (
                select(PaperTradeCertificate)
                .where(PaperTradeCertificate.strategy_id == cfg.strategy_id)
                .where(PaperTradeCertificate.asset_class == cfg.asset_class)
                .where(PaperTradeCertificate.version == cfg.version)
                .order_by(desc(PaperTradeCertificate.created_at))
                .limit(1)
            )
            cert = (await session.execute(cert_stmt)).scalar_one_or_none()
            cert_state = "no_cert"
            if cert is not None:
                if cert.status == "granted" and (
                    cert.validity_until is None
                    or cert.validity_until > datetime.now(timezone.utc)
                ):
                    cert_state = "valid"
                elif cert.status == "granted":
                    cert_state = "expired"
                else:
                    cert_state = cert.status
            out.append({
                "strategy_id": cfg.strategy_id,
                "asset_class": cfg.asset_class,
                "version": cfg.version,
                "cert_state": cert_state,
            })
    return out
