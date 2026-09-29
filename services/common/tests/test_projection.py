from __future__ import annotations

import pytest

from jalani_common.projection import project


def test_constant_demand_empties_at_the_expected_tick() -> None:
    p = project(inventory=1000, capacity=5000, demand_mean=[100] * 20, demand_sd=[0] * 20,
                tick_hours=0.25)  # fmt: skip
    assert p.hours_p50 == 2.5  # 10 ticks x 15 min
    assert p.stockout_prob == 1.0


def test_arrival_is_clamped_to_capacity() -> None:
    arrivals = [0.0] * 20
    arrivals[1] = 10_000  # far more than fits
    p = project(inventory=500, capacity=2000, demand_mean=[100] * 20, demand_sd=[0] * 20,
                arrivals=arrivals, tick_hours=0.25)  # fmt: skip
    # 500 - 100 = 400, +arrival clamps to 2000, -100 per tick -> empty 20 ticks after tick 1
    assert p.hours_p50 is None  # not within 20 ticks
    assert p.min_inventory_p50 == pytest.approx(100)


def test_outage_ticks_do_not_consume() -> None:
    serving = [False] * 5 + [True] * 15
    p = project(inventory=1000, capacity=5000, demand_mean=[100] * 20, demand_sd=[0] * 20,
                serving=serving, tick_hours=0.25)  # fmt: skip
    assert p.hours_p50 == 3.75  # 5 idle ticks + 10 serving ticks


def test_probability_grows_with_uncertainty_and_falls_with_stock() -> None:
    def prob(inv: float, sd: float) -> float:
        return project(inventory=inv, capacity=1e6, demand_mean=[100] * 96,
                       demand_sd=[sd] * 96, tick_hours=0.25).stockout_prob  # fmt: skip

    assert prob(12_000, 5) < 0.01  # needs 9,600: safe
    assert prob(9_500, 5) > 0.5  # a bit short
    assert prob(10_000, 5) < prob(10_000, 30)
