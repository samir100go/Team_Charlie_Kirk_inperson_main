#!/usr/bin/env python3
"""Generate DOC-DERIVED fixtures from the BUP Fuel Supply Simulator Integration Guide.

These stand in for recorded fixtures until `make sim-probe` can run against a
live simulator. Every file is marked `_meta.source = "doc-derived"` with the guide
section it came from; fields the guide does not state literally are listed in
`_meta.inferred`. A recorded fixture with the same file name in fixtures/ wins
over the one in fixtures/doc_derived/ (see jalani_common tests' fixture loader).

    python scripts/make_doc_fixtures.py
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

OUT = (
    Path(__file__).resolve().parent.parent
    / "services"
    / "common"
    / "tests"
    / "fixtures"
    / "doc_derived"
)
GUIDE = "BUP_Fuel_Supply_Simulator_Integration_Guide_Final.pdf"
JSON_CT = {"content-type": "application/json"}
UNDOCUMENTED_MSG = "(message text not documented)"

# -- the documented world (guide §8) -------------------------------------------------

REGIONS = [
    {"id": "region-dhaka", "name": "Dhaka Division", "demand_factor": 1.00},
    {"id": "region-chattogram", "name": "Chattogram Division", "demand_factor": 1.08},
]


def _fuels(d: int, p: int, o: int) -> dict[str, int]:
    return {"DIESEL": d, "PETROL": p, "OCTANE": o}


DEPOTS = [
    {
        "id": "depot-gazipur",
        "name": "Gazipur Depot",
        "region_id": "region-dhaka",
        "status": "OPEN",
        "dispatch_capacity_per_tick": 12000,
        "capacity": _fuels(90000, 70000, 45000),
        "inventory": _fuels(60000, 45000, 26000),
    },
    {
        "id": "depot-patiya",
        "name": "Patiya Depot",
        "region_id": "region-chattogram",
        "status": "OPEN",
        "dispatch_capacity_per_tick": 11000,
        "capacity": _fuels(85000, 65000, 40000),
        "inventory": _fuels(55000, 42000, 24000),
    },
]

_STATIONS = [
    ("station-mirpur", "Mirpur Fuel Station", "region-dhaka", "urban_high",
     _fuels(15000, 14000, 9000), _fuels(9000, 9000, 5000)),
    ("station-tongi", "Tongi Fuel Station", "region-dhaka", "industrial",
     _fuels(18000, 9000, 6000), _fuels(11000, 6000, 3500)),
    ("station-karnaphuli", "Karnaphuli Fuel Station", "region-chattogram", "highway",
     _fuels(14000, 15000, 9000), _fuels(8500, 9500, 5200)),
    ("station-coxsbazar", "Coxsbazar Fuel Station", "region-chattogram", "regional",
     _fuels(12000, 12000, 7000), _fuels(7500, 7500, 4200)),
]  # fmt: skip
STATIONS = [
    {
        "id": sid,
        "name": name,
        "region_id": region,
        "status": "OPEN",
        "demand_profile": profile,
        "demand_multiplier": 1.0,
        "capacity": cap,
        "inventory": inv,
    }
    for sid, name, region, profile, cap, inv in _STATIONS
]

_ROUTES = [
    ("route-gazipur-mirpur", "depot-gazipur", "station-mirpur", 2, 7000),
    ("route-gazipur-tongi", "depot-gazipur", "station-tongi", 2, 6500),
    ("route-patiya-karnaphuli", "depot-patiya", "station-karnaphuli", 2, 7000),
    ("route-patiya-coxsbazar", "depot-patiya", "station-coxsbazar", 3, 6000),
    ("route-gazipur-karnaphuli", "depot-gazipur", "station-karnaphuli", 4, 5000),
    ("route-patiya-mirpur", "depot-patiya", "station-mirpur", 4, 5000),
]
ROUTES = [
    {
        "id": rid,
        "source_depot_id": depot,
        "destination_station_id": station,
        "transit_ticks": transit,
        "max_shipment": max_ship,
        "status": "AVAILABLE",
    }
    for rid, depot, station, transit, max_ship in _ROUTES
]

# -- single documented examples (guide §4, §5, §6, §7) ------------------------------------

ALLOCATION_ARRIVED = {
    "id": 1,
    "idempotency_key": "demo-001",
    "source_depot_id": "depot-gazipur",
    "destination_station_id": "station-mirpur",
    "route_id": "route-gazipur-mirpur",
    "fuel_type": "DIESEL",
    "quantity": 3000,
    "created_tick": 5,
    "departure_tick": 6,
    "expected_arrival_tick": 8,
    "actual_arrival_tick": 8,
    "status": "ARRIVED",
    "failure_reason": None,
}
ALLOCATION_PENDING = {
    **ALLOCATION_ARRIVED,
    "departure_tick": None,
    "expected_arrival_tick": None,
    "actual_arrival_tick": None,
    "status": "PENDING",
}
ALLOCATION_REQUEST = {
    "idempotency_key": "demo-001",
    "source_depot_id": "depot-gazipur",
    "destination_station_id": "station-mirpur",
    "route_id": "route-gazipur-mirpur",
    "fuel_type": "DIESEL",
    "quantity": 3000,
}
INSTANCE = {
    "id": 1,
    "scenario_id": "baseline",
    "scenario_version": "1.0",
    "seed": 12345,
    "sim_time": "2026-01-01T00:00:00+00:00",
    "tick": 0,
    "tick_minutes": 15,
    "status": "PAUSED",
}
EVENT_RESOLVED = {
    "id": 1,
    "type": "demand_spike",
    "start_tick": 8,
    "end_tick": 20,
    "status": "RESOLVED",
    "parameters": {"region_ids": ["region-dhaka"], "multiplier": 1.8},
}
SUPPLY_ARRIVAL = {
    "id": "supply-001",
    "depot_id": "depot-gazipur",
    "fuel_type": "DIESEL",
    "quantity": 18000,
    "planned_tick": 12,
    "actual_tick": None,
    "status": "SCHEDULED",
}
DEMAND_ROW = {
    "id": 100,
    "station_id": "station-mirpur",
    "fuel_type": "DIESEL",
    "tick": 12,
    "sim_time": "2026-01-01T03:00:00+00:00",
    "demand_liters": 95.123,
    "served_liters": 95.123,
    "unmet_liters": 0.0,
}
METRICS = {
    "served_demand_liters": 12345.678,
    "unmet_demand_liters": 234.567,
    "service_level": 0.981408,
    "allocation_liters": 9800.000,
    "allocation_failures": 2,
}
AUDIT_ROW = {
    "id": 142,
    "wall_time": "2026-01-01T00:15:00.512345+00:00",
    "sim_time": "2026-01-01T00:14:00+00:00",
    "tick": 0,
    "action": "allocation.created",
    "entity_type": "allocation",
    "entity_id": "1",
    "result": "OK",
    "metadata_json": {"quantity": 3000, "fuel_type": "DIESEL"},
}

# -- builders --------------------------------------------------------------------------------


def fixture(
    endpoint: str,
    method: str,
    path: str,
    status: int,
    *,
    body: Any = None,
    text: str | None = None,
    params: dict[str, Any] | None = None,
    request_json: Any = None,
    headers: dict[str, str] | None = None,
    doc_ref: str,
    envelope: str | None = None,
    inferred: list[str] | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "source": "doc-derived",
        "endpoint": endpoint,
        "doc": GUIDE,
        "doc_ref": doc_ref,
        "replace_with": "make sim-probe (writes the recorded fixture with the same name)",
    }
    if envelope:
        meta["envelope"] = envelope
    if inferred:
        meta["inferred"] = inferred
    if note:
        meta["note"] = note
    response: dict[str, Any] = {"status": status, "headers": headers or dict(JSON_CT)}
    if text is not None:
        response["text"] = text
    else:
        response["json"] = copy.deepcopy(body)
    return {
        "_meta": meta,
        "request": {"method": method, "path": path, "params": params, "json": request_json},
        "response": response,
    }


def domain_error(code: str, message: str | None = UNDOCUMENTED_MSG) -> dict[str, Any]:
    detail: dict[str, Any] = {"code": code}
    if message is not None:
        detail["message"] = message
    return {"detail": detail}


def build() -> dict[str, dict[str, Any]]:
    fx: dict[str, dict[str, Any]] = {}

    # Reads (§4)
    fx["health__ok"] = fixture(
        "health", "GET", "/v1/health", 200,
        body={"status": "ok", "database": "ok", "simulation": {"status": "PAUSED", "tick": 0}},
        doc_ref="§4.1",
    )  # fmt: skip
    fx["instance__ok"] = fixture(
        "instance", "GET", "/v1/instance", 200, body=INSTANCE, doc_ref="§4.2"
    )
    fx["regions__ok"] = fixture("regions", "GET", "/v1/regions", 200, body=REGIONS, doc_ref="§4.4")
    fx["depots__ok"] = fixture(
        "depots", "GET", "/v1/depots", 200, body=DEPOTS, doc_ref="§4.5 example + §8.2 table",
        inferred=["depot-patiya.name"],
    )  # fmt: skip
    fx["depot__ok"] = fixture(
        "depot", "GET", "/v1/depots/depot-gazipur", 200, body=DEPOTS[0], doc_ref="§4.5"
    )
    fx["depot__not_found"] = fixture(
        "depot", "GET", "/v1/depots/depot-nowhere", 404,
        body=domain_error("NOT_FOUND", message=None), doc_ref="§4.5", envelope="detail_object",
        note="guide shows the code only; no message field",
    )  # fmt: skip
    fx["stations__ok"] = fixture(
        "stations", "GET", "/v1/stations", 200, body=STATIONS, doc_ref="§4.6 example + §8.3 table",
        inferred=["station-tongi.name", "station-karnaphuli.name", "station-coxsbazar.name"],
    )  # fmt: skip
    fx["station__ok"] = fixture(
        "station", "GET", "/v1/stations/station-mirpur", 200, body=STATIONS[0], doc_ref="§4.6"
    )
    fx["station__not_found"] = fixture(
        "station", "GET", "/v1/stations/station-nowhere", 404,
        body=domain_error("NOT_FOUND", message=None), doc_ref="§4.5 (same 404 rule as depots)",
        envelope="detail_object", inferred=["whole response: guide states it for depots only"],
    )  # fmt: skip
    fx["routes__ok"] = fixture(
        "routes", "GET", "/v1/routes", 200, body=ROUTES, doc_ref="§4.7 example + §8.4 table"
    )
    fx["supply_arrivals__ok"] = fixture(
        "supply_arrivals", "GET", "/v1/supply-arrivals", 200, body=[SUPPLY_ARRIVAL],
        doc_ref="§4.8", note="real list has 22 arrivals (§8.7); only one example is documented",
    )  # fmt: skip
    fx["events__ok"] = fixture(
        "events", "GET", "/v1/events", 200, body=[EVENT_RESOLVED], doc_ref="§4.9"
    )
    fx["events__empty"] = fixture(
        "events", "GET", "/v1/events", 200, body=[], doc_ref="§2 (default scenario has no events)"
    )
    fx["allocations__ok"] = fixture(
        "allocations", "GET", "/v1/allocations", 200, body=[ALLOCATION_ARRIVED], doc_ref="§4.10"
    )
    fx["demand_history__ok"] = fixture(
        "demand_history", "GET", "/v1/demand-history", 200, body=[DEMAND_ROW],
        params={"station_id": "station-mirpur", "limit": 200}, doc_ref="§4.11",
    )  # fmt: skip
    fx["metrics__ok"] = fixture("metrics", "GET", "/v1/metrics", 200, body=METRICS, doc_ref="§4.12")

    # POST /v1/allocations (§5, §9)
    fx["create_allocation__created"] = fixture(
        "create_allocation", "POST", "/v1/allocations", 201,
        body=ALLOCATION_PENDING, request_json=ALLOCATION_REQUEST, doc_ref="§5.3",
    )  # fmt: skip
    fx["create_allocation__replay"] = fixture(
        "create_allocation", "POST", "/v1/allocations", 201,
        body=ALLOCATION_PENDING, request_json=ALLOCATION_REQUEST, doc_ref="§5.4",
        note="§5.4 says replay returns 201; the §9 cheat sheet says 200 (see *_replay_status_200)",
    )  # fmt: skip
    fx["create_allocation__replay_status_200"] = fixture(
        "create_allocation", "POST", "/v1/allocations", 200,
        body=ALLOCATION_PENDING, request_json=ALLOCATION_REQUEST, doc_ref="§9 cheat sheet",
        note="contradicts §5.4 (201); the client must accept both",
    )  # fmt: skip
    errors_409 = {
        "key_mismatch": ("IDEMPOTENCY_KEY_MISMATCH", {**ALLOCATION_REQUEST, "quantity": 3100}),
        "route_mismatch": (
            "ROUTE_MISMATCH",
            {**ALLOCATION_REQUEST, "destination_station_id": "station-tongi"},
        ),
        "depot_closed": ("DEPOT_CLOSED", ALLOCATION_REQUEST),
        "station_closed": ("STATION_CLOSED", ALLOCATION_REQUEST),
        "route_disrupted": ("ROUTE_DISRUPTED", ALLOCATION_REQUEST),
        "route_capacity_exceeded": (
            "ROUTE_CAPACITY_EXCEEDED", {**ALLOCATION_REQUEST, "quantity": 7001},
        ),
        "insufficient_inventory": ("INSUFFICIENT_INVENTORY", ALLOCATION_REQUEST),
        "dispatch_capacity_exceeded": ("DISPATCH_CAPACITY_EXCEEDED", ALLOCATION_REQUEST),
        "destination_capacity_exceeded": ("DESTINATION_CAPACITY_EXCEEDED", ALLOCATION_REQUEST),
    }  # fmt: skip
    for case, (code, req) in errors_409.items():
        fx[f"create_allocation__{case}"] = fixture(
            "create_allocation", "POST", "/v1/allocations", 409,
            body=domain_error(code), request_json=req, doc_ref="§5.2, §9",
            envelope="detail_object", inferred=["detail.message"],
        )  # fmt: skip
    fx["create_allocation__not_found"] = fixture(
        "create_allocation", "POST", "/v1/allocations", 404,
        body=domain_error("NOT_FOUND"),
        request_json={**ALLOCATION_REQUEST, "source_depot_id": "depot-nowhere"},
        doc_ref="§5.2, §9", envelope="detail_object", inferred=["detail.message"],
    )  # fmt: skip
    fx["validation_error__quantity_zero"] = fixture(
        "create_allocation", "POST", "/v1/allocations", 422,
        body={
            "detail": [
                {
                    "type": "greater_than",
                    "loc": ["body", "quantity"],
                    "msg": "Input should be greater than 0",
                    "input": 0,
                    "ctx": {"gt": 0},
                }
            ]
        },
        request_json={**ALLOCATION_REQUEST, "quantity": 0}, doc_ref="§9 (FastAPI default)",
        envelope="detail_list",
        inferred=["detail[] item shape: FastAPI/Pydantic v2 default, not shown in the guide"],
    )  # fmt: skip

    # POST /v1/allocations/{id}/cancel (§5.5)
    fx["cancel_allocation__ok"] = fixture(
        "cancel_allocation", "POST", "/v1/allocations/1/cancel", 200,
        body={**ALLOCATION_PENDING, "status": "CANCELLED"}, doc_ref="§5.5",
    )  # fmt: skip
    fx["cancel_allocation__not_found"] = fixture(
        "cancel_allocation", "POST", "/v1/allocations/999999/cancel", 404,
        body=domain_error("ALLOCATION_NOT_FOUND"), doc_ref="§5.5, §9",
        envelope="detail_object", inferred=["detail.message"],
    )  # fmt: skip
    fx["cancel_allocation__cannot_cancel"] = fixture(
        "cancel_allocation", "POST", "/v1/allocations/1/cancel", 409,
        body=domain_error("CANNOT_CANCEL"), doc_ref="§5.5, §9",
        envelope="detail_object", inferred=["detail.message"],
    )  # fmt: skip

    # Faults on /v1/* (§7.10, §6.4)
    fx["fault_latency__instance"] = fixture(
        "instance", "GET", "/v1/instance", 200, body=INSTANCE, doc_ref="§7.10",
        note="latency adds delay_ms (default 500) before the response; payload unchanged",
    )  # fmt: skip
    fx["fault_latency__instance"]["_meta"]["elapsed_ms"] = 500
    fx["fault_unavailable__instance"] = fixture(
        "instance", "GET", "/v1/instance", 503,
        body={
            "error": {"code": "FAULT_INJECTED", "message": "Simulator API temporarily unavailable."}
        },
        doc_ref="§7.10", envelope="error_object",
    )  # fmt: skip
    fx["fault_error_rate__instance_503"] = fixture(
        "instance", "GET", "/v1/instance", 503,
        body={"error": {"code": "FAULT_INJECTED", "message": "Injected transient API error."}},
        doc_ref="§7.10", envelope="error_object",
    )  # fmt: skip
    fx["fault_error_rate__instance_200"] = fixture(
        "instance", "GET", "/v1/instance", 200, body=INSTANCE, doc_ref="§7.10",
        note="with probability 1-rate the request passes through untouched",
    )  # fmt: skip
    fx["fault_stale_data__instance"] = fixture(
        "instance", "GET", "/v1/instance", 200, body=INSTANCE,
        headers={**JSON_CT, "x-simulator-stale": "true"}, doc_ref="§3, §6.4, §7.10",
    )  # fmt: skip
    fx["fault_stream_disconnect__stream"] = fixture(
        "stream", "GET", "/v1/stream", 503,
        body={"detail": {"code": "FAULT_INJECTED"}}, doc_ref="§6.4, §7.10", envelope="sse_fault",
        note="nested under detail (not error), no message field",
    )  # fmt: skip

    # Admin (§7)
    fx["admin_step__ok"] = fixture(
        "admin_step", "POST", "/admin/step", 200,
        body={"tick": 6, "sim_time": "2026-01-01T01:30:00+00:00"}, doc_ref="§7.5",
    )  # fmt: skip
    fx["admin_faults_clear__ok"] = fixture(
        "admin_clear_faults", "POST", "/admin/faults/clear", 200,
        body={"status": "cleared"}, doc_ref="§7.11",
    )  # fmt: skip
    fx["admin_events_create__demand_spike"] = fixture(
        "admin_create_event", "POST", "/admin/events", 201,
        body={**EVENT_RESOLVED, "status": "SCHEDULED"},
        request_json={
            "type": "demand_spike", "start_tick": 8, "duration_ticks": 12,
            "parameters": {"region_ids": ["region-dhaka"], "multiplier": 1.8},
        },
        doc_ref="§7.7 request + §4.9 event shape",
        inferred=["response body: guide says 'the persisted event'; shape taken from §4.9"],
    )  # fmt: skip
    fx["admin_audit__ok"] = fixture(
        "admin_audit", "GET", "/admin/audit", 200, body=[AUDIT_ROW],
        params={"limit": 200}, doc_ref="§7.12",
    )  # fmt: skip

    # SSE (§6)
    alloc_changed = {**ALLOCATION_PENDING, "status": "IN_TRANSIT", "departure_tick": 6,
                     "expected_arrival_tick": 8}  # fmt: skip
    inventory = {"entity_type": "depot", "entity_id": "depot-gazipur",
                 "inventory": _fuels(57000, 45000, 26000)}  # fmt: skip
    events: list[tuple[str, dict[str, Any]]] = [
        ("simulation.tick", {"tick": 6, "sim_time": "2026-01-01T01:30:00+00:00"}),
        ("allocation.status_changed", alloc_changed),
        ("inventory.updated", inventory),
        ("simulator.notice", {"message": "Simulation reset"}),
    ]
    raw_lines = [": connected", ""]
    for name, data in events:
        raw_lines += [f"event: {name}", f"data: {json.dumps(data)}", ""]
    raw_lines += [": keepalive", ""]
    stream = fixture(
        "stream", "GET", "/v1/stream", 200, text="\n".join(raw_lines),
        headers={"content-type": "text/event-stream; charset=utf-8"}, doc_ref="§6.1, §6.3",
        inferred=["content-type charset", "event ordering", "inventory numbers"],
    )  # fmt: skip
    stream["response"]["raw_lines"] = raw_lines
    stream["response"]["events"] = [{"event": n, "data": d} for n, d in events]
    fx["stream__sample"] = stream
    for name, data in [*events, ("simulator.notice", {"level": "error", "message": "..."})]:
        key = name.replace(".", "_")
        if name == "simulator.notice" and "level" in data:
            key += "_error"
        fx[f"stream_event__{key}"] = {
            "_meta": {"source": "doc-derived", "endpoint": "stream_event", "doc": GUIDE,
                      "doc_ref": "§6.3"},
            "event": name,
            "data": data,
        }  # fmt: skip
    return fx


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    fixtures = build()
    for old in OUT.glob("*.json"):
        if old.stem not in fixtures:
            old.unlink()
    for name, payload in sorted(fixtures.items()):
        (OUT / f"{name}.json").write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {len(fixtures)} doc-derived fixtures to {OUT}")


if __name__ == "__main__":
    main()
