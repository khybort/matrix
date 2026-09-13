"""compute_fitness: variance-penalised, sample-size-scaled selection score."""

from __future__ import annotations

from decimal import Decimal

from labs.evaluate import compute_fitness


def test_zero_or_single_sample():
    assert compute_fitness(n=0, mean=Decimal("1"), std=Decimal("1")) == 0
    # n=1: no penalty, factor sqrt(1/25)=0.2
    assert compute_fitness(n=1, mean=Decimal("0.5"), std=Decimal("0")) == Decimal("0.1")


def test_variance_lowers_fitness_at_equal_mean():
    calm = compute_fitness(n=25, mean=Decimal("0.10"), std=Decimal("0.05"))
    wild = compute_fitness(n=25, mean=Decimal("0.10"), std=Decimal("0.60"))
    assert calm > wild
    assert calm == Decimal("0.09")  # 0.10 - 0.05/5
    assert wild < 0  # 0.10 - 0.12 → a noisy 'winner' no longer promotes


def test_more_samples_shrink_penalty_and_grow_factor():
    few = compute_fitness(n=9, mean=Decimal("0.10"), std=Decimal("0.30"))
    many = compute_fitness(n=100, mean=Decimal("0.10"), std=Decimal("0.30"))
    assert many > few
