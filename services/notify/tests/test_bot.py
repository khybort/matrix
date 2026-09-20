"""Auth + push API tests for the Telegram bot.

We don't spin up a real Telegram client; we mock the Application's
.bot.send_message and assert routing decisions.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from notify.bot import (
    ALERT_INFO,
    ALERT_URGENT,
    get_allowed_chat_ids,
    push,
)

pytestmark = pytest.mark.asyncio


def test_allowlist_parses_csv(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123, 456, 789")
    assert get_allowed_chat_ids() == {123, 456, 789}


def test_allowlist_ignores_blanks_and_junk(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", " 42 , , not-a-number, 9 ")
    assert get_allowed_chat_ids() == {42, 9}


def test_allowlist_empty_when_unset(monkeypatch):
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    assert get_allowed_chat_ids() == set()


async def test_push_drops_when_no_allowlist(monkeypatch):
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    app = MagicMock()
    app.bot.send_message = AsyncMock()
    sent = await push(app, ALERT_INFO, "test")
    assert sent == 0
    app.bot.send_message.assert_not_called()


async def test_push_fans_out_to_all_allowed_chats(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "1,2,3")
    app = MagicMock()
    app.bot.send_message = AsyncMock()
    sent = await push(app, ALERT_URGENT, "alert!")
    assert sent == 3
    assert app.bot.send_message.await_count == 3


async def test_push_continues_when_one_chat_fails(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "1,2,3")
    app = MagicMock()
    # Second call raises; others succeed
    calls = {"n": 0}

    async def _fake_send(**_kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("transient telegram api error")
    app.bot.send_message = AsyncMock(side_effect=_fake_send)
    sent = await push(app, ALERT_INFO, "x")
    # 2 successes, 1 failure
    assert sent == 2
    assert app.bot.send_message.await_count == 3


def _update(chat_id: int):
    upd = MagicMock()
    upd.effective_chat.id = chat_id
    upd.message.reply_text = AsyncMock()
    return upd


async def test_dev_accept_routes_to_client_with_chat_identity(monkeypatch):
    from notify import bot as B
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "1")
    calls = []

    async def fake_accept(task_id, *, by):
        calls.append((task_id, by))
        return "✅ task #7 merged."
    monkeypatch.setattr(B.dev_client, "accept", fake_accept)
    ctx = MagicMock(); ctx.args = ["#7"]
    upd = _update(1)
    await B.cmd_dev_accept(upd, ctx)
    assert calls == [(7, "telegram:1")]
    upd.message.reply_text.assert_awaited_once_with("✅ task #7 merged.")


async def test_dev_commands_ignore_unauthorized_and_validate_usage(monkeypatch):
    from notify import bot as B
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "1")

    async def never(*a, **k):
        raise AssertionError("must not call dev_agent")
    monkeypatch.setattr(B.dev_client, "accept", never)
    monkeypatch.setattr(B.dev_client, "revise", never)
    ctx = MagicMock(); ctx.args = ["7"]
    stranger = _update(999)
    await B.cmd_dev_accept(stranger, ctx)
    stranger.message.reply_text.assert_not_awaited()
    bad = _update(1); ctx.args = ["seven"]
    await B.cmd_dev_accept(bad, ctx)
    bad.message.reply_text.assert_awaited_once()
    assert "Usage" in bad.message.reply_text.await_args.args[0]
    ctx.args = ["7"]  # revise without notes
    bad2 = _update(1)
    await B.cmd_dev_revise(bad2, ctx)
    assert "Usage" in bad2.message.reply_text.await_args.args[0]


# --- getUpdates conflict watch -------------------------------------------


def _conflict_record() -> "logging.LogRecord":
    import logging

    from telegram.error import Conflict

    try:
        raise Conflict("terminated by other getUpdates request")
    except Conflict:
        import sys

        return logging.LogRecord(
            "telegram.ext.Updater", logging.ERROR, __file__, 1,
            "Exception happened while polling for updates.", (), sys.exc_info(),
        )


def test_conflict_filter_counts_and_drops_the_traceback():
    """A contested token must cost one counted line, not a stack trace every
    40 seconds — and the count is what the operator alert is keyed on."""
    from notify import bot as B

    B.CONFLICTS.update({"count": 0, "first_at": 0.0, "last_at": 0.0})
    f = B._ConflictFilter()
    rec = _conflict_record()
    assert f.filter(rec) is True          # the record is kept, not swallowed
    assert rec.exc_info is None           # but the traceback is gone
    assert B.CONFLICTS["count"] == 1
    assert B.CONFLICTS["first_at"] > 0
    assert "another consumer holds this bot token" in (rec.msg % rec.args)

    f.filter(_conflict_record())
    assert B.CONFLICTS["count"] == 2


def test_conflict_filter_leaves_unrelated_errors_alone():
    import logging

    from notify import bot as B

    B.CONFLICTS.update({"count": 0, "first_at": 0.0, "last_at": 0.0})
    rec = logging.LogRecord(
        "telegram.ext.Updater", logging.ERROR, __file__, 1, "network down", (), None
    )
    assert B._ConflictFilter().filter(rec) is True
    assert B.CONFLICTS["count"] == 0
    assert rec.msg == "network down"
