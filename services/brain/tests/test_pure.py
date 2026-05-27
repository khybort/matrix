"""Host-side unit tests for the Brain's dependency-light logic."""

from __future__ import annotations

from brain.access import make_deny_hook
from brain.history import format_history
from brain.sse import sse_frame
from brain.tier import choose_tier


# ---- tier routing ----

def test_market_query_routes_to_local():
    assert choose_tier("SELECT * FROM market_bars WHERE symbol='BTCUSDT'") == "local"


def test_domain_query_routes_to_shared():
    assert choose_tier("SELECT * FROM predictions ORDER BY created_at DESC") == "shared"


def test_mixed_query_with_any_local_table_routes_local():
    assert choose_tier(
        "SELECT * FROM predictions p JOIN market_bars b ON b.symbol = p.symbol"
    ) == "local"


# ---- SSE framing ----

def test_sse_frame_encodes_dict_as_json():
    out = sse_frame("tool_call", {"name": "cypher_query"})
    assert out == 'event: tool_call\ndata: {"name":"cypher_query"}\n\n'


def test_sse_frame_passes_string_through():
    assert sse_frame("token", "hello") == "event: token\ndata: hello\n\n"


# ---- history formatting ----

def test_format_history_appends_current_user_turn():
    out = format_history(
        [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
        "why short BTC?",
    )
    assert out.splitlines() == ["USER: hi", "ASSISTANT: hello", "USER: why short BTC?"]


def test_format_history_with_no_prior_turns():
    assert format_history([], "first question") == "USER: first question"


# ---- deny hook ----

def test_deny_hook_allows_listed_tool():
    hook = make_deny_hook({"mcp__matrix__cypher_query", "mcp__matrix__sql_read"})
    assert hook("mcp__matrix__cypher_query", {}) is True


def test_deny_hook_blocks_builtin_write_tools():
    hook = make_deny_hook({"mcp__matrix__cypher_query"})
    assert hook("Bash", {}) is False
    assert hook("Write", {"file_path": "/x"}) is False


def test_deny_hook_blocks_unknown_tool():
    hook = make_deny_hook({"mcp__matrix__sql_read"})
    assert hook("mcp__matrix__delete_everything", {}) is False
