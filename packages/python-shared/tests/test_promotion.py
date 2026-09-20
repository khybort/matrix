"""The promotion bar: deflated Sharpe, the pre-registered stopping rule, and
the registry that must not move once written."""

from __future__ import annotations

import json
import random

import pytest

from matrix_shared import promotion as P
from matrix_shared.edge_study import benjamini_hochberg, benjamini_yekutieli


# --- normal quantiles ---------------------------------------------------


def test_ppf_matches_known_quantiles():
    assert abs(P._ppf(0.975) - 1.959964) < 1e-4
    assert abs(P._ppf(0.80) - 0.841621) < 1e-4
    assert abs(P._ppf(0.5)) < 1e-9
    assert P._ppf(0.001) < -3.0 and P._ppf(0.999) > 3.0


# --- deflated Sharpe ----------------------------------------------------


def test_deflated_sharpe_separates_a_real_edge_from_noise():
    """The whole point: a strategy picked as best-of-13 must beat the expected
    maximum of 13 worthless strategies, not merely beat zero."""
    random.seed(7)
    real = [random.gauss(0.20, 1.0) for _ in range(800)]
    noise = [random.gauss(0.00, 1.0) for _ in range(800)]
    d_real = P.deflated_sharpe(real, n_trials=13)
    d_noise = P.deflated_sharpe(noise, n_trials=13)
    assert d_real["dsr"] > 0.99
    assert d_noise["dsr"] < 0.10
    # The null it must clear is strictly above zero because we searched.
    assert d_real["sharpe_null"] > 0.0


def test_deflating_for_more_trials_raises_the_bar():
    random.seed(11)
    xs = [random.gauss(0.07, 1.0) for _ in range(600)]
    few = P.deflated_sharpe(xs, n_trials=2)
    many = P.deflated_sharpe(xs, n_trials=500)
    assert many["sharpe_null"] > few["sharpe_null"]
    assert many["dsr"] < few["dsr"]


def test_deflated_sharpe_declines_to_answer_on_a_thin_or_flat_sample():
    assert P.deflated_sharpe([0.1] * 10, n_trials=13) is None      # too few
    assert P.deflated_sharpe([0.1] * 100, n_trials=13) is None     # no variance


# --- the pre-registered stopping rule -----------------------------------


def test_required_trades_matches_the_registered_target_for_momentum_xs():
    """+33.2 bps at sd 164.8 — halve the edge for the winner's curse and the
    honest sample size is ~800, which is what we pre-registered."""
    n = P.required_trades(33.2, 164.8)
    assert 700 <= n <= 950


def test_a_smaller_edge_demands_a_bigger_sample():
    assert P.required_trades(15.0, 164.8) > P.required_trades(33.2, 164.8)
    assert P.required_trades(80.0, 164.8) < P.required_trades(33.2, 164.8)


def test_required_trades_is_capped_and_refuses_nonsense():
    assert P.required_trades(0.0, 164.8) is None
    assert P.required_trades(33.2, 0.0) is None
    assert P.required_trades(0.0001, 500.0) == P.MAX_REQUIRED_N
    assert P.required_trades(33.2, 164.8) >= P.MIN_REQUIRED_N


# --- the registry -------------------------------------------------------


def test_registration_is_written_once_and_never_moves(tmp_path):
    """A pre-registration that can be rewritten after seeing the data is not a
    pre-registration."""
    reg = P.Registry()
    first = reg.register("momentum_xs", "crypto", edge_bps=33.2, sd_bps=164.8)
    again = reg.register("momentum_xs", "crypto", edge_bps=900.0, sd_bps=1.0)
    assert again == first
    assert first["required_n"] == P.required_trades(33.2, 164.8)

    path = tmp_path / "edge_registry.json"
    reg.save(path)
    assert json.loads(path.read_text())["momentum_xs/crypto"]["required_n"] == first["required_n"]
    assert P.Registry.load(path).get("momentum_xs", "crypto") == first


def test_registry_survives_a_missing_or_corrupt_file(tmp_path):
    assert P.Registry.load(tmp_path / "nope.json").entries == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert P.Registry.load(bad).entries == {}


# --- status -------------------------------------------------------------


def _row(n: int, dsr: float = 0.999) -> dict:
    return {"strategy": "momentum_xs", "market": "crypto", "n": n, "dsr": dsr}


def test_confirmation_also_requires_surviving_deflation():
    """bist_news_event reached its registered count on 33 trades with a DSR of
    0.53 — a coin flip after deflating for having searched thirteen strategies.
    Reaching the count is necessary, not sufficient."""
    reg = P.Registry()
    reg.register("momentum_xs", "crypto", edge_bps=33.2, sd_bps=164.8)
    need = reg.get("momentum_xs", "crypto")["required_n"]
    weak = P.status(_row(need, dsr=0.53), registry=reg, n_trials=13, significant=True)
    strong = P.status(_row(need, dsr=0.99), registry=reg, n_trials=13, significant=True)
    assert weak == "provisional"
    assert strong == "confirmed"


def test_a_large_effect_size_cannot_register_a_tiny_target():
    """Without a floor, a huge measured effect registers a target of a few dozen
    trades and 'confirmed' arrives on exactly the small sample the bar exists
    to distrust."""
    assert P.required_trades(400.0, 100.0) == P.MIN_REQUIRED_N
    assert P.MIN_REQUIRED_N >= 200


def test_status_walks_from_provisional_to_confirmed_or_failed(tmp_path):
    reg = P.Registry()
    reg.register("momentum_xs", "crypto", edge_bps=33.2, sd_bps=164.8)
    need = reg.get("momentum_xs", "crypto")["required_n"]

    # Short of its own target, a significant result is still only provisional.
    assert P.status(_row(need - 1), registry=reg, n_trials=13, significant=True) == "provisional"
    # Reached the target and still significant — this is the only state that
    # earns full size.
    assert P.status(_row(need), registry=reg, n_trials=13, significant=True) == "confirmed"
    # Reached the target and lost significance: the hypothesis had its chance.
    assert P.status(_row(need), registry=reg, n_trials=13, significant=False) == "failed"
    assert P.status(None, registry=reg, n_trials=13, significant=True) == "unproven"


def test_unregistered_strategy_is_never_confirmed():
    reg = P.Registry()
    assert P.status(_row(100000), registry=reg, n_trials=13, significant=True) == "provisional"


# --- dependency-safe FDR ------------------------------------------------


def test_benjamini_yekutieli_is_strictly_stricter_than_bh():
    """Our thirteen tests share bars, symbols and windows, so BH's independence
    assumption does not hold; BHY is valid under arbitrary dependence."""
    ps = [0.001, 0.004, 0.02, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99]
    bh, by = benjamini_hochberg(ps), benjamini_yekutieli(ps)
    assert sum(by) <= sum(bh)
    assert sum(bh) > sum(by)  # this sample is chosen to separate them
    # Everything BHY keeps, BH keeps too.
    assert all(b for b, y in zip(bh, by) if y)


def test_benjamini_yekutieli_on_an_empty_set():
    assert benjamini_yekutieli([]) == []


# --- the bar actually gates capital --------------------------------------


def _verdict_row(status=None, **kw):
    r = dict(
        strategy="momentum_xs", market="crypto", n=1000,
        t=5.0, edge_bps=30.0, t_side=5.0, side_edge_bps=30.0,
    )
    r.update(kw)
    if status is not None:
        r["status"] = status
    return r


def test_only_a_confirmed_strategy_earns_the_paying_verdict():
    """`pays` is the verdict Kelly sizing and the EV floor act on, so the
    promotion bar has to reach it or it is decoration."""
    from matrix_shared.edge_study import verdict

    assert verdict(_verdict_row("confirmed"), cost_bps=12.0) == "pays"
    assert verdict(_verdict_row("provisional"), cost_bps=12.0) == "unproven"
    assert verdict(_verdict_row("failed"), cost_bps=12.0) == "unproven"


def test_a_row_without_status_keeps_the_old_behaviour():
    """An older cached row, or a registry this process cannot read, must not
    starve the book — the promotion machinery is advisory, the risk gates are
    not."""
    from matrix_shared.edge_study import verdict

    assert verdict(_verdict_row(None), cost_bps=12.0) == "pays"


def test_an_inverted_strategy_is_still_harmful_whatever_its_status():
    """Being unproven and being backwards are different findings, and only one
    of them is fixed by collecting more data."""
    from matrix_shared.edge_study import verdict

    row = _verdict_row("provisional", t=-5.0, edge_bps=-30.0, t_side=-5.0, side_edge_bps=-30.0)
    assert verdict(row, cost_bps=12.0) == "harmful"


# --- the study must never block the trading loop -------------------------

pytestmark_async = pytest.mark.asyncio


@pytest.mark.asyncio
async def test_strategy_edge_returns_immediately_and_refreshes_behind(monkeypatch):
    """The caller is a 5-second trading loop. A cache miss must hand back what
    we have (or None) and schedule the work, never run it inline: on 2026-09-20
    an inline study stalled the paper engine for twenty minutes."""
    import asyncio

    from matrix_shared import edge_study as E

    E.clear_cache()
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_study(*, days, strategy_id=None):
        started.set()
        await release.wait()
        return [{"market": "crypto", "strategy": strategy_id, "n": 500}]

    monkeypatch.setattr(E, "run_edge_study", slow_study)

    first = await E.strategy_edge("momentum_xs", "crypto")
    assert first is None                      # nothing cached yet, and we did not wait
    await asyncio.wait_for(started.wait(), timeout=2)

    # A second call while the refresh is in flight must not queue another study.
    assert await E.strategy_edge("momentum_xs", "crypto") is None
    assert len(E._refreshing) == 1

    release.set()
    for _ in range(50):
        await asyncio.sleep(0.01)
        if not E._refreshing:
            break
    row = await E.strategy_edge("momentum_xs", "crypto")
    assert row is not None and row["n"] == 500
    E.clear_cache()


@pytest.mark.asyncio
async def test_a_failing_study_does_not_respawn_a_task_every_tick(monkeypatch):
    import asyncio

    from matrix_shared import edge_study as E

    E.clear_cache()
    calls = []

    async def boom(*, days, strategy_id=None):
        calls.append(strategy_id)
        raise RuntimeError("no bars")

    monkeypatch.setattr(E, "run_edge_study", boom)
    for _ in range(5):
        assert await E.strategy_edge("grid", "crypto") is None
        await asyncio.sleep(0.02)
    assert len(calls) == 1          # cached miss, not one study per tick
    E.clear_cache()
