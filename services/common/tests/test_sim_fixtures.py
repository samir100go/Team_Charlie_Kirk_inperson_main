"""Pin the simulator behaviour we recorded in Phase 0 (docs/SIMULATOR_NOTES.md).

These run offline against the fixtures. If a re-recording (`make sim-probe`)
changes any of this, the simulator changed and the sim client must follow.
"""

from __future__ import annotations

from datetime import datetime

import pytest

import simfixtures as fx

DOCUMENTED_ENDPOINTS = {
    "health": "health__ok",
    "instance": "instance__ok",
    "regions": "regions__ok",
    "depots": "depots__ok",
    "depot": "depot__ok",
    "stations": "stations__ok",
    "station": "station__ok",
    "routes": "routes__ok",
    "supply_arrivals": "supply_arrivals__ok",
    "events": "events__ok",
    "allocations": "allocations__ok",
    "demand_history": "demand_history__ok",
    "metrics": "metrics__ok",
    "create_allocation": "create_allocation__created",
    "cancel_allocation": "cancel_allocation__ok",
    "stream": "stream__sample",
    "admin_run": "admin_run__ok",
    "admin_pause": "admin_pause__ok",
    "admin_toggle": "admin_toggle__ok",
    "admin_step": "admin_step__ok",
    "admin_reset": "admin_reset__ok",
    "admin_create_event": "admin_events_create__demand_spike",
    "admin_create_fault": "admin_faults_create__latency",
    "admin_clear_faults": "admin_faults_clear__ok",
    "admin_audit": "admin_audit__ok",
    "admin_faults": "admin_faults__list",
    "admin_events": "admin_events__list",
}
FAULTS = ("latency", "unavailable", "error_rate", "stale_data", "stream_disconnect")
ALLOCATION_409S = (
    "IDEMPOTENCY_KEY_MISMATCH",
    "ROUTE_MISMATCH",
    "STATION_CLOSED",
    "ROUTE_DISRUPTED",
    "ROUTE_CAPACITY_EXCEEDED",
    "INSUFFICIENT_INVENTORY",
    "DISPATCH_CAPACITY_EXCEEDED",
    "DESTINATION_CAPACITY_EXCEEDED",
)
DOC_ONLY = {"create_allocation__depot_closed", "stream_event__simulator_notice_error"}


@pytest.mark.parametrize(("endpoint", "name"), sorted(DOCUMENTED_ENDPOINTS.items()))
def test_every_documented_endpoint_is_recorded(endpoint: str, name: str) -> None:
    assert fx.source(name) == "recorded", endpoint


@pytest.mark.parametrize("fault", FAULTS)
def test_every_fault_type_is_recorded(fault: str) -> None:
    assert fx.response(f"admin_faults_create__{fault}")["status"] == 201
    assert fx.source(f"fault_{fault}__stream") == "recorded"


def test_only_unrecordable_cases_are_doc_derived() -> None:
    doc_derived = {n for n in fx.names() if fx.source(n) == "doc-derived"}
    assert doc_derived == DOC_ONLY


@pytest.mark.parametrize("code", ALLOCATION_409S)
def test_allocation_409s_use_detail_envelope_with_message(code: str) -> None:
    name = f"create_allocation__{code.lower()}"
    if code == "IDEMPOTENCY_KEY_MISMATCH":
        name = "create_allocation__key_mismatch"
    resp = fx.response(name)
    assert resp["status"] == 409
    assert resp["json"]["detail"]["code"] == code
    assert resp["json"]["detail"]["message"]


def test_read_and_cancel_404s_have_code_but_no_message() -> None:
    for name, code in [
        ("depot__not_found", "NOT_FOUND"),
        ("station__not_found", "NOT_FOUND"),
        ("cancel_allocation__not_found", "ALLOCATION_NOT_FOUND"),
    ]:
        assert fx.response(name)["status"] == 404
        assert fx.body(name) == {"detail": {"code": code}}
    # ...while POST /v1/allocations says which id was unknown.
    assert fx.body("create_allocation__not_found")["detail"]["message"]


def test_fault_envelopes() -> None:
    for name in ("fault_unavailable__instance", "fault_error_rate__instance_503"):
        assert fx.response(name)["status"] == 503
        assert fx.body(name)["error"]["code"] == "FAULT_INJECTED"
    stream = fx.response("fault_stream_disconnect__stream")
    assert stream["status"] == 503
    assert stream["json"] == {"detail": {"code": "FAULT_INJECTED"}}
    # `unavailable` also refuses the stream, with the *error* envelope.
    assert fx.response("fault_unavailable__stream")["json"]["error"]["code"] == "FAULT_INJECTED"


def test_validation_errors_are_fastapi_lists() -> None:
    for name in ("validation_error__quantity_zero", "validation_error__bad_fuel"):
        assert fx.response(name)["status"] == 422
        assert isinstance(fx.body(name)["detail"], list)


def test_idempotent_replay_returns_201_with_same_allocation() -> None:
    created, replay = (
        fx.response("create_allocation__created"),
        fx.response("create_allocation__replay"),
    )
    assert created["status"] == replay["status"] == 201  # guide §9 says 200: wrong
    assert replay["json"]["id"] == created["json"]["id"]


def test_replay_of_cancelled_key_returns_the_cancelled_allocation() -> None:
    resp = fx.response("create_allocation__replay_after_cancel")
    assert resp["status"] == 201
    assert resp["json"]["status"] == "CANCELLED"


def test_sim_time_is_naive_but_fault_wall_times_are_aware() -> None:
    for sim_time in (
        fx.body("instance__ok")["sim_time"],
        fx.body("admin_step__ok")["sim_time"],
        fx.body("demand_history__ok")[0]["sim_time"],
    ):
        assert datetime.fromisoformat(sim_time).tzinfo is None
    fault = fx.body("admin_faults_create__latency")
    assert datetime.fromisoformat(fault["start_wall_time"]).tzinfo is not None


def test_demand_history_order_and_limits() -> None:
    rows = fx.body("demand_history__ok")
    ids = [r["id"] for r in rows]
    assert ids == sorted(ids, reverse=True)  # newest first
    assert len(fx.body("demand_history__limit_zero")) == 1  # clamped, not 422
    unknown = fx.response("demand_history__unknown_station")
    assert (unknown["status"], unknown["json"]) == (200, [])  # not 404
    all_stations = {r["station_id"] for r in fx.body("demand_history__no_station_id")}
    assert len(all_stations) > 1  # station_id is optional


def test_stale_header_is_on_gets_and_on_the_stream() -> None:
    assert fx.response("fault_stale_data__instance")["headers"]["x-simulator-stale"] == "true"
    assert fx.response("fault_stale_data__stream")["headers"]["x-simulator-stale"] == "true"
    assert "x-simulator-stale" not in fx.response("instance__ok")["headers"]


def test_stream_only_announces_api_driven_changes() -> None:
    events = fx.response("stream__sample")["events"]
    changed = [e["data"]["status"] for e in events if e["event"] == "allocation.status_changed"]
    # create, create, cancel -- then 4 ticks of departures/arrivals produce nothing.
    assert changed == ["PENDING", "PENDING", "CANCELLED"]
    inventory = [e["data"]["entity_type"] for e in events if e["event"] == "inventory.updated"]
    assert set(inventory) == {"depot"}
    assert events[-1] == {"event": "simulator.notice", "data": {"message": "Simulation reset"}}
    ticks = [e["data"]["tick"] for e in events if e["event"] == "simulation.tick"]
    assert ticks == [1, 2, 3, 4]


def test_world_matches_guide_section_8() -> None:
    depots = {d["id"]: d for d in fx.body("depots__ok")}
    assert depots["depot-gazipur"]["dispatch_capacity_per_tick"] == 12000
    assert depots["depot-patiya"]["dispatch_capacity_per_tick"] == 11000
    assert len(fx.body("stations__ok")) == 4
    assert len(fx.body("routes__ok")) == 6
    assert len(fx.body("supply_arrivals__ok")) == 22
    assert fx.body("instance__ok")["seed"] == 12345
