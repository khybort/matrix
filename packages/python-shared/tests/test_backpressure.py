import pytest

pytestmark = pytest.mark.asyncio

from matrix_shared import backpressure as BP


def test_cap_scales_with_slots_and_has_a_floor(monkeypatch):
    monkeypatch.setattr(BP, "BACKLOG_MULT", 5)
    monkeypatch.setattr(BP, "BACKLOG_MIN", 3)
    monkeypatch.setattr(BP, "DEFAULT_SLOTS", 2)
    assert BP.cap_for_slots(1) == 5
    assert BP.cap_for_slots(6) == 30
    assert BP.cap_for_slots(0) == 3          # paused strategy keeps a probe-sized backlog
    assert BP.cap_for_slots(None) == 10      # no slot row yet → DEFAULT_SLOTS


async def test_room_is_cap_minus_backlog_and_never_negative(monkeypatch):
    monkeypatch.setattr(BP, "BACKLOG_MULT", 5)
    monkeypatch.setattr(BP, "BACKLOG_MIN", 3)

    async def slots(sid, ac):
        return 1

    async def have(sid, ac, *, shadow=False):
        return 7 if not shadow else 0
    monkeypatch.setattr(BP, "champion_slots", slots)
    monkeypatch.setattr(BP, "backlog", have)
    assert await BP.room("s", "crypto") == 0
    assert await BP.room("s", "crypto", shadow=True) == 5


async def test_room_falls_back_to_min_on_probe_failure(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(BP, "champion_slots", boom)
    monkeypatch.setattr(BP, "BACKLOG_MIN", 3)
    assert await BP.room("s", "crypto") == 3
