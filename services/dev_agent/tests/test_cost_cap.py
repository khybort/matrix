"""tests/test_cost_cap.py"""

from __future__ import annotations

import pytest

from dev_agent.safety import CostCap, CostCapError


def test_cost_cap_under_limit_does_not_raise():
    cap = CostCap(per_task_cap_usd=5.00)
    cap.add(1.0); cap.add(2.0); cap.add(1.5)
    assert cap.total == pytest.approx(4.5)


def test_cost_cap_at_limit_raises():
    cap = CostCap(per_task_cap_usd=5.00)
    cap.add(3.0)
    with pytest.raises(CostCapError) as excinfo:
        cap.add(2.5)
    assert "task cost cap" in str(excinfo.value).lower()


def test_cost_cap_zero_value_ok():
    cap = CostCap(per_task_cap_usd=5.00)
    cap.add(0.0)
    assert cap.total == 0.0


def test_cost_cap_negative_value_ignored():
    cap = CostCap(per_task_cap_usd=5.00)
    cap.add(-1.0)
    assert cap.total == 0.0
