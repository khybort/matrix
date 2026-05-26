"""Telegram bot glue: command handlers + auth + push API.

Auth model: a comma-separated allowlist of chat_ids in
TELEGRAM_ALLOWED_CHAT_IDS env. Any message from a chat_id not on the
list gets logged and silently ignored — we don't even reply, because
the alternative leaks system existence to anyone who finds the bot.

Push API: `push(level, text)` ships a message to every allowed chat.
Used by the alert poller in main.py.
"""

from __future__ import annotations

import os

from loguru import logger
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from notify.alerts import (
    ALERT_INFO,
    ALERT_URGENT,
    ALERT_WARNING,
    format_circuit,
    format_help,
    format_status,
    format_strategies,
)
from notify.state import (
    get_active_strategies,
    get_default_wallet,
    get_open_positions_summary,
    get_recent_pnl,
)


def get_allowed_chat_ids() -> set[int]:
    """Allowlist from env. Empty = nobody (bot won't respond to anyone)."""
    raw = os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "").strip()
    if not raw:
        return set()
    out: set[int] = set()
    for s in raw.split(","):
        s = s.strip()
        if not s:
            continue
        try:
            out.add(int(s))
        except ValueError:
            logger.warning(f"invalid chat_id in allowlist: {s!r}")
    return out


def _authorized(update: Update, allowlist: set[int]) -> bool:
    chat = update.effective_chat
    if chat is None:
        return False
    if chat.id in allowlist:
        return True
    logger.info(f"ignoring message from unauthorized chat_id={chat.id}")
    return False


# ----------------------------------------------------------------- handlers


async def cmd_start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    allowlist = get_allowed_chat_ids()
    chat = update.effective_chat
    if chat is None:
        return
    if chat.id not in allowlist:
        # First-contact hint with the chat_id so the operator can add it to env.
        await update.message.reply_text(
            f"Unauthorized. If you're the operator, add this chat_id to "
            f"TELEGRAM_ALLOWED_CHAT_IDS in .env:\n\n`{chat.id}`",
            parse_mode=ParseMode.MARKDOWN,
        )
        logger.info(f"unauth /start from chat_id={chat.id}")
        return
    await update.message.reply_text(
        format_help(), parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_status(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update, get_allowed_chat_ids()):
        return
    wallet = await get_default_wallet()
    positions = (
        await get_open_positions_summary(wallet.wallet_id) if wallet else {"total": 0, "by_strategy": {}}
    )
    pnl = await get_recent_pnl(24)
    await update.message.reply_text(
        format_status(wallet, positions, pnl), parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_strategies(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update, get_allowed_chat_ids()):
        return
    rows = await get_active_strategies()
    await update.message.reply_text(
        format_strategies(rows), parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_circuit(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update, get_allowed_chat_ids()):
        return
    wallet = await get_default_wallet()
    await update.message.reply_text(
        format_circuit(wallet), parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_help(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update, get_allowed_chat_ids()):
        return
    await update.message.reply_text(
        format_help(), parse_mode=ParseMode.MARKDOWN,
    )


# ----------------------------------------------------------------- builder


def build_application(token: str) -> Application:
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("strategies", cmd_strategies))
    app.add_handler(CommandHandler("circuit", cmd_circuit))
    app.add_handler(CommandHandler("help", cmd_help))
    return app


# ----------------------------------------------------------------- push API


async def push(app: Application, level: str, text: str) -> int:
    """Fan-out a message to every allowed chat_id. Returns count sent.

    `level` is informational (one of ALERT_URGENT / WARNING / INFO) — used
    here only to log; the chat message itself already carries the visual
    cue via the leading emoji.
    """
    allowlist = get_allowed_chat_ids()
    if not allowlist:
        logger.warning(f"push: no allowed chat_ids; dropping {level} alert: {text[:80]!r}")
        return 0
    sent = 0
    for chat_id in allowlist:
        try:
            await app.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
            )
            sent += 1
        except Exception as e:
            logger.exception(f"push to chat_id={chat_id} failed: {e}")
    logger.info(f"pushed {level} alert to {sent}/{len(allowlist)} chats")
    return sent


__all__ = [
    "ALERT_INFO",
    "ALERT_URGENT",
    "ALERT_WARNING",
    "build_application",
    "get_allowed_chat_ids",
    "push",
]
