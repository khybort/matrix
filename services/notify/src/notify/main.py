"""notify daemon entry point.

Runs two coroutines in the same event loop:
  - Telegram polling for incoming commands (handled by python-telegram-bot)
  - Alert poller that snapshots wallet/cert state every N seconds and
    fans out change-driven alerts via the bot's push API.

The bot token comes from TELEGRAM_BOT_TOKEN. If unset, the daemon logs
a warning and runs the poller in dry-run mode (alerts logged, not sent)
— useful so a partial-config node still surfaces issues in logs.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys

from loguru import logger

from notify.alerts import (
    ALERT_INFO,
    PollSnapshot,
    detect_alerts,
)
from notify.bot import build_application, push
from notify.state import (
    get_active_strategies,
    get_default_wallet,
    get_open_positions_summary,
    get_recent_pnl,
)

DEFAULT_POLL_INTERVAL_S = 60.0


async def _certs_keymap() -> dict[tuple[str, str, int], str]:
    """Flatten active_strategies into the key→state map detect_alerts wants."""
    rows = await get_active_strategies()
    return {
        (r["strategy_id"], r["asset_class"], r["version"]): r["cert_state"]
        for r in rows
    }


async def _daily_summary_text() -> str:
    wallet = await get_default_wallet()
    pnl = await get_recent_pnl(24)
    positions = (
        await get_open_positions_summary(wallet.wallet_id) if wallet else {"total": 0}
    )
    if wallet is None:
        return "ℹ️ Daily summary: no wallet found."
    win_rate_str = "—"
    if pnl.get("win_rate") is not None:
        win_rate_str = f"{pnl['win_rate'] * 100:.1f}%"
    return (
        "ℹ️ *Daily summary*\n"
        f"  Equity: `{wallet.equity_usd:.2f} USD` "
        f"(net `{wallet.net_pnl_pct:+.3f}%`)\n"
        f"  Open positions: `{positions['total']}`\n"
        f"  Last 24h: `{pnl['n_outcomes']}` scored, "
        f"PnL `{pnl['total_pnl_usd']} USD`, win-rate `{win_rate_str}`"
    )


async def _poll_loop(
    app, interval_s: float, stop: asyncio.Event, *, dry_run: bool = False
) -> None:
    snap = PollSnapshot()
    # Seed once before going into the loop so the first tick has a baseline.
    try:
        snap.wallet = await get_default_wallet()
        snap.active_certs = await _certs_keymap()
        snap.live_execution_enabled = (
            os.environ.get("LIVE_EXECUTION_ENABLED", "false").strip().lower() == "true"
        )
        from datetime import datetime, timezone
        snap.last_daily_summary_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        logger.info(
            f"poller seeded: wallet={'yes' if snap.wallet else 'no'} "
            f"active_strategies={len(snap.active_certs)}"
        )
    except Exception as e:
        logger.exception(f"poller seed failed (will retry): {e}")

    while not stop.is_set():
        try:
            wallet = await get_default_wallet()
            certs = await _certs_keymap()
            alerts, snap = detect_alerts(snap, wallet, certs)
        except Exception as e:
            logger.exception(f"poller tick failed: {e}")
            alerts = []

        for level, text in alerts:
            # Special sentinel from alerts.py — replace with the actual summary.
            if text.startswith("__DAILY_SUMMARY_DUE__"):
                try:
                    text = await _daily_summary_text()
                except Exception as e:
                    logger.exception(f"daily summary fetch failed: {e}")
                    continue
            if dry_run or app is None:
                logger.info(f"[dry-run] {level}: {text}")
            else:
                await push(app, level, text)

        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except TimeoutError:
            pass


async def run(token: str | None, poll_interval_s: float) -> None:
    stop = asyncio.Event()

    def _handle_signal(*_: object) -> None:
        logger.info("shutdown signal received")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    if not token:
        logger.warning(
            "TELEGRAM_BOT_TOKEN unset; running in dry-run mode (alerts log-only)"
        )
        await _poll_loop(None, poll_interval_s, stop, dry_run=True)
        return

    app = build_application(token)
    # python-telegram-bot v21 wants explicit lifecycle for "run alongside
    # other coroutines" (no `run_polling` shortcut here).
    await app.initialize()
    await app.start()
    if app.updater is not None:
        await app.updater.start_polling()
    logger.info("telegram bot started; allowed chats configured")

    try:
        await _poll_loop(app, poll_interval_s, stop)
    finally:
        try:
            if app.updater is not None:
                await app.updater.stop()
            await app.stop()
            await app.shutdown()
        except Exception:
            logger.exception("telegram bot shutdown failed (continuing)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Matrix notify daemon")
    parser.add_argument(
        "--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL_S,
        help=f"Alert poll interval seconds (default {DEFAULT_POLL_INTERVAL_S})",
    )
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level: <5} | {message}")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or None
    logger.info(f"notify start: poll_interval={args.poll_interval}s "
                f"token={'set' if token else 'unset (dry-run)'}")

    asyncio.run(run(token, args.poll_interval))


if __name__ == "__main__":
    main()


# Re-export so `from notify.main import ...` discovery is obvious.
__all__ = ["main", "run", "ALERT_INFO"]
