"""Telegram bot glue: command handlers + auth + push API.

Auth model: a comma-separated allowlist of chat_ids in
TELEGRAM_ALLOWED_CHAT_IDS env. Any message from a chat_id not on the
list gets logged and silently ignored — we don't even reply, because
the alternative leaks system existence to anyone who finds the bot.

Push API: `push(level, text)` ships a message to every allowed chat.
Used by the alert poller in main.py.
"""

from __future__ import annotations

import logging
import os
import time

from loguru import logger
from telegram import Update
from telegram.error import Conflict
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from notify.alerts import (
    ALERT_INFO,
    ALERT_URGENT,
    ALERT_WARNING,
    format_circuit,
    format_help,
    format_status,
    format_strategies,
)
from notify import dev_client
from notify.brain_client import ask_brain
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


async def cmd_ask(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Route free-text (non-command) messages to the read-only Brain.

    Unauthorized chats are silently ignored (same policy as commands). The
    Telegram chat id is the Brain session's external_ref, so each chat is a
    continuous multi-turn conversation.
    """
    if not _authorized(update, get_allowed_chat_ids()):
        return
    chat = update.effective_chat
    question = (update.message.text or "").strip()
    if not question:
        return
    await _ctx.bot.send_chat_action(chat_id=chat.id, action=ChatAction.TYPING)
    answer = await ask_brain(question, external_ref=str(chat.id))
    # Brain answers are plain text; send without Markdown parsing so stray
    # underscores/asterisks in data don't trip Telegram's parser.
    await update.message.reply_text(answer, disable_web_page_preview=True)


async def cmd_circuit_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Operator-only reset of a tripped daily-loss circuit (docs/TRADING.md #2:
    with live execution enabled the circuit never auto-resets). Usage:
    /circuit_reset <asset_class> [wallet_name]"""
    if not _authorized(update, get_allowed_chat_ids()):
        return
    args = list(ctx.args or [])
    if not args:
        await update.message.reply_text("Usage: /circuit_reset <asset_class> [wallet_name]")
        return
    asset_class = args[0].strip().lower()
    name = args[1].strip() if len(args) > 1 else "default"
    from sqlalchemy import text as _text
    from matrix_shared import shared_session_scope
    async with shared_session_scope() as session:
        rows = (await session.execute(_text(
            "UPDATE wallets SET circuit_tripped_at = NULL, day_start_equity = cash_usd + locked_usd, "
            "day_start_at = now(), updated_at = now() "
            "WHERE asset_class = :ac AND name = :n AND circuit_tripped_at IS NOT NULL RETURNING name"
        ), {"ac": asset_class, "n": name})).all()
    chat = update.effective_chat
    if rows:
        logger.warning(f"circuit reset by operator chat_id={chat.id}: {name}/{asset_class}")
        await update.message.reply_text(f"🟢 Circuit reset for {name}/{asset_class}. New day baseline = current equity.")
    else:
        await update.message.reply_text(f"Nothing to reset: {name}/{asset_class} is not tripped (or not found).")


def _task_id(ctx: ContextTypes.DEFAULT_TYPE) -> int | None:
    args = list(ctx.args or [])
    try:
        return int(str(args[0]).lstrip("#"))
    except (IndexError, ValueError):
        return None


async def cmd_dev_tasks(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update, get_allowed_chat_ids()):
        return
    await update.message.reply_text(await dev_client.list_awaiting())


async def cmd_dev_accept(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Merge an awaiting_review dev_agent task into main. Usage: /dev_accept <id>"""
    if not _authorized(update, get_allowed_chat_ids()):
        return
    task_id = _task_id(ctx)
    if task_id is None:
        await update.message.reply_text("Usage: /dev_accept <task_id>")
        return
    by = f"telegram:{update.effective_chat.id}"
    logger.warning(f"dev task #{task_id} accept requested by {by}")
    await update.message.reply_text(await dev_client.accept(task_id, by=by))


async def cmd_dev_discard(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update, get_allowed_chat_ids()):
        return
    task_id = _task_id(ctx)
    if task_id is None:
        await update.message.reply_text("Usage: /dev_discard <task_id>")
        return
    by = f"telegram:{update.effective_chat.id}"
    await update.message.reply_text(await dev_client.discard(task_id, by=by))


async def cmd_dev_revise(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update, get_allowed_chat_ids()):
        return
    task_id = _task_id(ctx)
    notes = " ".join(list(ctx.args or [])[1:]).strip()
    if task_id is None or not notes:
        await update.message.reply_text("Usage: /dev_revise <task_id> <what to change>")
        return
    await update.message.reply_text(await dev_client.revise(task_id, notes))


# ----------------------------------------------------------------- builder


# Long poll for LONG_POLL_S, and give the HTTP read a margin on top of it.
# When the client read deadline lands *inside* a poll the server is still
# holding, the retry races the request Telegram has not closed yet and every
# few minutes the log fills with `Conflict: terminated by other getUpdates
# request` — which reads exactly like a second bot instance and is not one.
# On 2026-09-20 that cost an investigation: nine consecutive long polls with
# the container stopped never once returned 409, so no competitor existed.
LONG_POLL_S = float(os.environ.get("MATRIX_TG_LONG_POLL_S", "30"))
LONG_POLL_READ_MARGIN_S = float(os.environ.get("MATRIX_TG_READ_MARGIN_S", "10"))


def build_application(token: str) -> Application:
    app = (
        Application.builder()
        .token(token)
        .get_updates_read_timeout(LONG_POLL_S + LONG_POLL_READ_MARGIN_S)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("strategies", cmd_strategies))
    app.add_handler(CommandHandler("circuit", cmd_circuit))
    app.add_handler(CommandHandler("circuit_reset", cmd_circuit_reset))
    app.add_handler(CommandHandler("dev_tasks", cmd_dev_tasks))
    app.add_handler(CommandHandler("dev_accept", cmd_dev_accept))
    app.add_handler(CommandHandler("dev_discard", cmd_dev_discard))
    app.add_handler(CommandHandler("dev_revise", cmd_dev_revise))
    app.add_handler(CommandHandler("help", cmd_help))
    # Any non-command text becomes a question for the Brain.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_ask))
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

# --------------------------------------------------- getUpdates conflict watch

# Telegram delivers each update to exactly ONE getUpdates caller. When a second
# consumer holds the same bot token, it both terminates our long poll (logging a
# full traceback every time) and *takes* updates that were meant for us — so a
# command the operator sends can silently land somewhere else. On 2026-09-20
# this was measured from three independent vantage points with our own poller
# stopped: a lone traced poller inside the container (3 conflicts in 240 s at
# inflight=1), plain host-side long polls (2 of 8 returned 409), and an
# inventory showing no local container or process holding the token. The only
# remedy is revoking the token in BotFather; until then we count the conflicts,
# keep the log to one line, and tell the operator over the channel that still
# works (sending is unaffected — only receiving is contested).

CONFLICTS: dict[str, float | int] = {"count": 0, "first_at": 0.0, "last_at": 0.0}


class _ConflictFilter(logging.Filter):
    """Collapse PTB's per-conflict traceback into one counted warning line."""

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        if not isinstance(exc, Conflict):
            return True
        now = time.time()
        CONFLICTS["count"] = int(CONFLICTS["count"]) + 1
        CONFLICTS["last_at"] = now
        if not CONFLICTS["first_at"]:
            CONFLICTS["first_at"] = now
        record.exc_info = None
        record.exc_text = None
        record.msg = (
            "getUpdates conflict #%d — another consumer holds this bot token; "
            "updates may be going to it instead of us"
        )
        record.args = (CONFLICTS["count"],)
        return True


def install_conflict_filter() -> None:
    """Attach the filter to the loggers PTB polls from."""
    for name in ("telegram.ext.Updater", "telegram.ext._updater"):
        logging.getLogger(name).addFilter(_ConflictFilter())


def conflicts_since(epoch_s: float) -> int:
    """Conflicts counted since `epoch_s` (cheap: the counter is monotonic)."""
    return int(CONFLICTS["count"]) if CONFLICTS["last_at"] >= epoch_s else 0

