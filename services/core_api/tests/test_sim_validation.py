"""Every real recorded simulator payload passes validation; corrupted ones are rejected."""

from __future__ import annotations

import copy

import pytest

import simfixtures as fx
from core_api.chaos import Corruption
from core_api.sim_models import InvalidSimulatorPayload, validate

RECORDED = {
    "/v1/instance": "instance__ok",
    "/v1/regions": "regions__ok",
    "/v1/stations": "stations__with_outage",
    "/v1/depots": "depots__with_constrained",
    "/v1/routes": "routes__with_disruption",
    "/v1/events": "events__active_mixed",
    "/v1/allocations": "allocations__ok",
    "/v1/metrics": "metrics__after_allocations",
    "/v1/demand-history": "demand_history__ok",
    "/v1/supply-arrivals": "supply_arrivals__after_delay_shortfall",
}


@pytest.mark.parametrize(("endpoint", "fixture"), sorted(RECORDED.items()))
def test_recorded_payloads_are_valid(endpoint: str, fixture: str) -> None:
    body = fx.body(fixture)
    assert validate(endpoint, body) is body


def test_corrupted_stations_are_rejected_with_readable_errors() -> None:
    corruption = Corruption()
    corruption.until = float("inf")
    broken = corruption.apply("/v1/stations", copy.deepcopy(fx.body("stations__ok")))
    with pytest.raises(InvalidSimulatorPayload) as exc:
        validate("/v1/stations", broken)
    joined = " ".join(exc.value.errors)
    assert "inventory.DIESEL" in joined
    assert "status" in joined


def test_missing_field_and_negative_inventory_are_rejected() -> None:
    depots = copy.deepcopy(fx.body("depots__ok"))
    depots[0]["inventory"]["PETROL"] = -5
    del depots[1]["dispatch_capacity_per_tick"]
    with pytest.raises(InvalidSimulatorPayload):
        validate("/v1/depots", depots)
