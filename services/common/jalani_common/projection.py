"""Inventory projection under a demand forecast (pure math, shared by intelligence and core-api).

Order of operations inside one simulator tick (docs/SIMULATOR_NOTES.md §1): arrivals land
(clamped to capacity), then demand is served. A station in OUTAGE serves nothing, so its
inventory does not move. Demand per tick is Normal(mean_t, sd_t); a stockout by tick c
happens when cumulative demand exceeds what was available by then.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

Z90 = 1.2815515655446004  # P90 / P10 of a standard normal


def normal_sf(x: float) -> float:
    """P(Z > x) for a standard normal."""
    return 0.5 * math.erfc(x / math.sqrt(2.0))


@dataclass(frozen=True)
class Projection:
    hours_p50: float | None  # None = no stockout within the horizon
    hours_early: float | None  # with P90 demand (pessimistic)
    hours_late: float | None  # with P10 demand (optimistic)
    stockout_prob: float  # P(stockout within the horizon)
    horizon_hours: float
    min_inventory_p50: float

    def as_dict(self) -> dict[str, float | None]:
        return {
            "hours_p50": self.hours_p50,
            "hours_early": self.hours_early,
            "hours_late": self.hours_late,
            "stockout_prob": round(self.stockout_prob, 4),
            "horizon_hours": self.horizon_hours,
            "min_inventory_p50": round(self.min_inventory_p50, 1),
        }


def _first_empty(
    inventory: float,
    capacity: float,
    arrivals: Sequence[float],
    demand: Sequence[float],
    active: Sequence[bool],
) -> tuple[int | None, float]:
    inv, low = inventory, inventory
    for k, (arr, dem, on) in enumerate(zip(arrivals, demand, active, strict=True)):
        inv = min(capacity, inv + arr)
        if on:
            inv -= dem
        low = min(low, inv)
        if inv <= 0:
            return k, low
    return None, low


def project(
    *,
    inventory: float,
    capacity: float,
    demand_mean: Sequence[float],
    demand_sd: Sequence[float],
    arrivals: Sequence[float] | None = None,
    serving: Sequence[bool] | None = None,
    tick_hours: float,
) -> Projection:
    """Project one station x fuel over len(demand_mean) ticks.

    `arrivals[k]` is fuel landing at the start of future tick k; `serving[k]` is False while
    the station is in OUTAGE. Times are hours from now to the end of the tick that empties it.
    """
    n = len(demand_mean)
    arr = list(arrivals) if arrivals is not None else [0.0] * n
    on = list(serving) if serving is not None else [True] * n
    hi = [m + Z90 * s for m, s in zip(demand_mean, demand_sd, strict=True)]
    lo = [max(0.0, m - Z90 * s) for m, s in zip(demand_mean, demand_sd, strict=True)]

    k50, low50 = _first_empty(inventory, capacity, arr, demand_mean, on)
    k_hi, _ = _first_empty(inventory, capacity, arr, hi, on)
    k_lo, _ = _first_empty(inventory, capacity, arr, lo, on)

    # P(stockout by tick c) for every c; arrivals are ignored once capacity would clip them
    # only in the mean path, which keeps this a conservative (slightly high) estimate.
    prob = 0.0
    cum_mean = cum_var = 0.0
    supply = inventory
    for k in range(n):
        supply = min(capacity + cum_mean, supply + arr[k])
        if on[k]:
            cum_mean += demand_mean[k]
            cum_var += demand_sd[k] ** 2
        if cum_var <= 0:
            p = 1.0 if cum_mean >= supply else 0.0
        else:
            p = normal_sf((supply - cum_mean) / math.sqrt(cum_var))
        prob = max(prob, p)

    def hours(k: int | None) -> float | None:
        return None if k is None else round((k + 1) * tick_hours, 2)

    return Projection(
        hours_p50=hours(k50),
        hours_early=hours(k_hi),
        hours_late=hours(k_lo),
        stockout_prob=min(1.0, max(0.0, prob)),
        horizon_hours=round(n * tick_hours, 2),
        min_inventory_p50=low50,
    )
