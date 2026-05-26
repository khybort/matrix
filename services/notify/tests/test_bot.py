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
