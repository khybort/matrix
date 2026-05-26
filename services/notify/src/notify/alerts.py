"""Alert condition detection + message formatting.

The poller calls `detect_alerts(prev, curr)` once per tick and gets back
a list of (level, message) tuples to push. State is kept in-memory in the
process — a restart re-fires whatever's still true, which is acceptable
for ops notifications (a duplicate alert after a restart is preferable
to a missed one).

Three levels:
  - URGENT   ⚠️  circuit tripped, LIVE_EXECUTION_ENABLED flipped, big drop
  - WARNING  ⚡  equity drift below soft threshold
  - INFO     ℹ️  cert granted, daily summary
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from notify.state import WalletSnapshot

ALERT_URGENT = "URGENT"
ALERT_WARNING = "WARNING"
ALERT_INFO = "INFO"

# Equity drop warning threshold (fraction of equity since last tick).
DEFAULT_EQUITY_DROP_PCT = Decimal("0.01")


@dataclass(slots=True)
class PollSnapshot:
    """Whatever the previous tick remembered. Drives change detection."""

    wallet: WalletSnapshot | None = None
    active_certs: dict[tuple[str, str, int], str] = field(default_factory=dict)
    # (strategy_id, asset_class, version) → cert_state at last tick
    live_execution_enabled: bool = False
    last_daily_summary_date: str | None = None  # ISO YYYY-MM-DD UTC


def _env_live_enabled() -> bool:
    return os.environ.get("LIVE_EXECUTION_ENABLED", "false").strip().lower() == "true"


def detect_alerts(
    prev: PollSnapshot,
    curr_wallet: WalletSnapshot | None,
    curr_certs: dict[tuple[str, str, int], str],
    *,
    equity_drop_pct: Decimal = DEFAULT_EQUITY_DROP_PCT,
    now: datetime | None = None,
) -> tuple[list[tuple[str, str]], PollSnapshot]:
    """Compare prev snapshot to current state; return (alerts, new_snapshot).

    Pure-ish: takes inputs, no DB calls. The caller fetches state and feeds
    it in. Returns a fresh PollSnapshot reflecting `curr_*`.
    """
    now = now or datetime.now(timezone.utc)
    alerts: list[tuple[str, str]] = []

    # 1. LIVE_EXECUTION_ENABLED flip → urgent.
    live_now = _env_live_enabled()
    if live_now and not prev.live_execution_enabled:
        alerts.append((
            ALERT_URGENT,
            "⚠️ LIVE_EXECUTION_ENABLED flipped to TRUE. "
            "Per-strategy cert gate still applies, but the kill-switch is now armed.",
        ))
    elif not live_now and prev.live_execution_enabled:
        alerts.append((
            ALERT_INFO,
            "ℹ️ LIVE_EXECUTION_ENABLED dropped to FALSE. Live order path closed.",
        ))

    # 2. Circuit tripped.
    if curr_wallet is not None:
        prev_tripped = prev.wallet.circuit_tripped_at if prev.wallet else None
        curr_tripped = curr_wallet.circuit_tripped_at
        if curr_tripped is not None and prev_tripped != curr_tripped:
            alerts.append((
                ALERT_URGENT,
                f"⚠️ Daily-loss circuit TRIPPED for wallet {curr_wallet.name} "
                f"at {curr_tripped.isoformat(timespec='seconds')}.\n"
                f"Equity {curr_wallet.equity_usd:.2f} USD, "
                f"net P&L {curr_wallet.net_pnl_pct:+.3f}%.",
            ))

        # 3. Equity drop since last tick (warning, not urgent).
        if prev.wallet is not None and prev.wallet.equity_usd > 0:
            delta_pct = (
                (curr_wallet.equity_usd - prev.wallet.equity_usd)
                / prev.wallet.equity_usd
            )
            if delta_pct <= -equity_drop_pct:
                alerts.append((
                    ALERT_WARNING,
                    f"⚡ Equity dropped {delta_pct * Decimal(100):.3f}% "
                    f"since last check: {prev.wallet.equity_usd:.2f} → "
                    f"{curr_wallet.equity_usd:.2f} USD.",
                ))

    # 4. Cert state changes.
    for key, new_state in curr_certs.items():
        old_state = prev.active_certs.get(key)
        if old_state == new_state:
            continue
        sid, ac, ver = key
        if new_state == "valid" and old_state != "valid":
            alerts.append((
                ALERT_INFO,
                f"ℹ️ paper_trade_certificate GRANTED for {sid}/{ac}/v{ver}. "
                "Strategy is now eligible for live execution (gate still checks env + caps).",
            ))
        elif new_state in ("expired", "revoked") and old_state == "valid":
            alerts.append((
                ALERT_WARNING,
                f"⚡ paper_trade_certificate for {sid}/{ac}/v{ver} is now {new_state}. "
                "Live execution blocked until re-grant.",
            ))

    # 5. Daily summary at first poll of a new UTC day. Only fires once per day.
    today = now.strftime("%Y-%m-%d")
    if prev.last_daily_summary_date is not None and prev.last_daily_summary_date != today:
        # Caller can attach the heavy summary; we just signal it's due.
        alerts.append((ALERT_INFO, f"__DAILY_SUMMARY_DUE__ {today}"))

    new_state = PollSnapshot(
        wallet=curr_wallet,
        active_certs=dict(curr_certs),
        live_execution_enabled=live_now,
        last_daily_summary_date=today,
    )
    return alerts, new_state


def format_status(wallet: WalletSnapshot | None, positions: dict[str, Any],
                  pnl: dict[str, Any]) -> str:
    """Pretty status message for /status command. Markdown-friendly."""
    if wallet is None:
        return "No wallet found. Has migrations been applied?"
    lines = [
        f"💼 *{wallet.name}*",
        f"  Equity: `{wallet.equity_usd:.2f} USD`",
        f"  Cash:   `{wallet.cash_usd:.2f} USD`",
        f"  Locked: `{wallet.locked_usd:.2f} USD`",
        f"  Net P&L: `{wallet.net_pnl_usd:+.2f} USD` "
        f"({wallet.net_pnl_pct:+.3f}%)",
    ]
    if wallet.circuit_tripped_at is not None:
        lines.append(f"  ⚠️ Circuit tripped: `{wallet.circuit_tripped_at.isoformat(timespec='seconds')}`")
    lines.append("")
    lines.append(f"📊 *Open positions: {positions['total']}*")
    for sid, info in positions.get("by_strategy", {}).items():
        lines.append(f"  `{sid}`: {info['n']} pos, notional ${info['notional_usd']}")
    lines.append("")
    win_rate_str = "—"
    if pnl.get("win_rate") is not None:
        win_rate_str = f"{pnl['win_rate'] * 100:.1f}%"
    lines.append(f"📈 *Last {pnl['window_hours']}h*: {pnl['n_outcomes']} scored, "
                 f"PnL `{pnl['total_pnl_usd']} USD`, win-rate `{win_rate_str}`")
    return "\n".join(lines)


def format_strategies(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No active strategies."
    lines = ["🧠 *Active strategies*"]
    for r in rows:
        emoji = {
            "valid": "✅",
            "no_cert": "○",
            "pending": "○",
            "expired": "⚡",
            "revoked": "⚠️",
        }.get(r["cert_state"], "?")
        lines.append(
            f"  {emoji} `{r['strategy_id']}/{r['asset_class']}/v{r['version']}` — cert: {r['cert_state']}"
        )
    return "\n".join(lines)


def format_circuit(wallet: WalletSnapshot | None) -> str:
    if wallet is None:
        return "No wallet."
    if wallet.circuit_tripped_at is None:
        return (
            f"🟢 *Circuit clean*\n"
            f"  Daily-loss threshold: `{wallet.daily_loss_circuit_pct * 100:.2f}%`\n"
            f"  Max position size:    `{wallet.max_position_pct * 100:.2f}%`\n"
            f"  Max concurrent:       `{wallet.max_concurrent_positions}`"
        )
    return (
        f"🔴 *Circuit tripped*\n"
        f"  At: `{wallet.circuit_tripped_at.isoformat(timespec='seconds')}`\n"
        f"  Equity: `{wallet.equity_usd:.2f} USD`\n"
        f"  Net P&L: `{wallet.net_pnl_pct:+.3f}%`"
    )


def format_help() -> str:
    return (
        "*matrix-notify*\n"
        "/status       — wallet, open positions, recent P&L\n"
        "/strategies   — active strategy configs + cert state\n"
        "/circuit      — daily-loss circuit breaker state\n"
        "/help         — this message\n\n"
        "Push alerts you'll get automatically:\n"
        "  ⚠️ circuit tripped\n"
        "  ⚠️ LIVE_EXECUTION_ENABLED flipped\n"
        "  ⚡ equity dropped > 1% since last check\n"
        "  ℹ️ paper_trade_certificate granted\n"
        "  ℹ️ daily summary (once per UTC day)"
    )
