"""Decision rule of the demo slice (core_api.slice.plan), on the recorded simulator world."""

from __future__ import annotations

import copy
from typing import Any

import pytest

import simfixtures as fx
from core_api.slice import RATE_WINDOW_TICKS, demand_rate_per_tick, plan

TICK_H = 0.25


def _rows(station: str, per_tick: dict[str, float], ticks: int = RATE_WINDOW_TICKS) -> list[Any]:
    """demand-history shape: newest first, one row per fuel per tick."""
    rows = []
    for t in range(ticks, 0, -1):
        for fuel, liters in per_tick.items():
            rows.append({"station_id": station, "fuel_type": fuel, "tick": t,
                         "demand_liters": liters})  # fmt: skip
    return rows


def _world(inventory: dict[str, dict[str, float]], per_tick: float = 100.0) -> dict[str, Any]:
    stations = copy.deepcopy(fx.body("stations__ok"))
    for s in stations:
        s["inventory"].update(inventory.get(s["id"], {}))
    return {
        "instance": fx.body("instance__ok"),
        "stations": stations,
        "depots": copy.deepcopy(fx.body("depots__ok")),
        "routes": copy.deepcopy(fx.body("routes__ok")),
        "events": [],
        "metrics": fx.body("metrics__initial"),
        "allocations": [],
        "demand": {
            s["id"]: _rows(s["id"], {"DIESEL": per_tick, "PETROL": per_tick, "OCTANE": per_tick})
            for s in stations
        },
    }


def test_rate_uses_only_the_newest_window_of_one_fuel() -> None:
    rows = _rows("s", {"DIESEL": 100.0, "PETROL": 999.0}, ticks=RATE_WINDOW_TICKS)
    rows += _rows("s", {"DIESEL": 5000.0}, ticks=4)  # older rows beyond the window
    assert demand_rate_per_tick(rows, "DIESEL") == 100.0


def test_recorded_demand_rate_matches_documented_profile() -> None:
    # Mirpur (urban_high, region 1.00, multiplier 1.0) at tick 12-19 = 03:00-04:45, off-peak 0.70.
    rows = fx.body("demand_history__ok")
    expected_per_tick = {"DIESEL": 8500 / 96 * 0.70, "PETROL": 10500 / 96 * 0.70,
                         "OCTANE": 5600 / 96 * 0.70}  # fmt: skip
    for fuel, expected in expected_per_tick.items():
        # documented noise is uniform +-10%; an 8-tick mean stays well inside it
        assert demand_rate_per_tick(rows, fuel) == pytest.approx(expected, rel=0.10)


def test_cover_after_refill_is_current_plus_incoming_plus_shipment_over_rate() -> None:
    w = _world({"station-mirpur": {"PETROL": 300.0}})
    w["allocations"] = [{"status": "IN_TRANSIT", "destination_station_id": "station-mirpur",
                         "fuel_type": "PETROL", "source_depot_id": "depot-gazipur",
                         "quantity": 1000.0}]  # fmt: skip
    recs = plan(w)["recommendations"]
    rec = next(r for r in recs if r["id"].startswith("t0-station-mirpur-PETROL"))
    rate_h = 100.0 / TICK_H
    expected = (300 + 1000 + rec["quantity"]) / rate_h
    assert rec["hours_cover_after"] == pytest.approx(expected, abs=0.051)  # shown to 0.1 h
    assert rec["incoming_l"] == 1000.0


def test_dispatch_capacity_goes_to_the_most_urgent_first() -> None:
    # Every Dhaka tank nearly empty: Gazipur (12,000 L/tick) cannot serve all six.
    low = {f: 50.0 for f in ("DIESEL", "PETROL", "OCTANE")}
    w = _world({"station-mirpur": dict(low), "station-tongi": dict(low)})
    w["stations"][0]["inventory"]["DIESEL"] = 10.0  # Mirpur DIESEL is the most urgent
    out = plan(w)
    by_depot: dict[str, float] = {}
    for r in out["recommendations"]:
        by_depot[r["depot_id"]] = by_depot.get(r["depot_id"], 0.0) + r["quantity"]
    caps = {d["id"]: d["dispatch_capacity_per_tick"] for d in w["depots"]}
    assert all(total <= caps[d] for d, total in by_depot.items())
    assert out["recommendations"][0]["station_id"] == "station-mirpur"
    assert out["recommendations"][0]["fuel"] == "DIESEL"
    urgency = [r["hours_to_stockout"] for r in out["recommendations"]]
    assert urgency == sorted(urgency)
    assert out["waiting"], "the rest must wait for next tick, not be shown as approvable"


def test_pending_allocations_use_up_this_ticks_capacity_and_are_not_reshipped() -> None:
    w = _world({"station-tongi": {"DIESEL": 100.0}, "station-mirpur": {"PETROL": 100.0}})
    w["allocations"] = [{"status": "PENDING", "destination_station_id": "station-tongi",
                         "fuel_type": "DIESEL", "source_depot_id": "depot-gazipur",
                         "quantity": 6500.0}]  # fmt: skip
    out = plan(w)
    assert not any(r["station_id"] == "station-tongi" and r["fuel"] == "DIESEL"
                   for r in out["recommendations"])  # fmt: skip
    gazipur = sum(r["quantity"] for r in out["recommendations"] if r["depot_id"] == "depot-gazipur")
    assert gazipur <= 12000 - 6500
