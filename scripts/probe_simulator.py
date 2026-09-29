#!/usr/bin/env python3
"""Record contract-test fixtures from a live BUP Fuel Supply Simulator.

Calls every /v1 and /admin endpoint, provokes every documented error code it can,
injects every fault type and samples the SSE stream. Each exchange is saved as
services/common/tests/fixtures/<endpoint>__<case>.json. A recorded fixture takes
precedence over the doc-derived one with the same name in fixtures/doc_derived/.

WARNING: this RESETS the simulator it points at (several times). Never point it
at a world you care about.

    python scripts/probe_simulator.py                     # all sections
    python scripts/probe_simulator.py --only faults,stream
    SIMULATOR_URL=http://localhost:8001 python scripts/probe_simulator.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from simlib import (
    DEFAULT_BASE_URL,
    FIXTURES_DIR,
    SIMULATOR_IMAGE,
    Rec,
    Sim,
    SSECapture,
    now_iso,
    write_json,
)

GAZIPUR, PATIYA = "depot-gazipur", "depot-patiya"
MIRPUR, TONGI = "station-mirpur", "station-tongi"
KARNAPHULI, COXSBAZAR = "station-karnaphuli", "station-coxsbazar"
R_GAZ_MIR, R_GAZ_TON = "route-gazipur-mirpur", "route-gazipur-tongi"
R_PAT_KAR, R_PAT_COX = "route-patiya-karnaphuli", "route-patiya-coxsbazar"
R_GAZ_KAR, R_PAT_MIR = "route-gazipur-karnaphuli", "route-patiya-mirpur"

FAULTS: list[tuple[str, dict[str, Any]]] = [
    ("latency", {"delay_ms": 300}),
    ("unavailable", {}),
    ("error_rate", {"rate": 0.5}),
    ("stale_data", {}),
    ("stream_disconnect", {}),
]


class Recorder:
    def __init__(self, sim: Sim, out: Path) -> None:
        self.sim = sim
        self.out = out
        self.written: list[str] = []

    def save(self, name: str, rec: Rec, endpoint: str, note: str | None = None) -> Rec:
        extra = {"sim_tick": self._safe_tick()}
        write_json(self.out / f"{name}.json", rec.to_fixture(endpoint, note=note, extra=extra))
        self.written.append(name)
        code = f" {rec.code}" if rec.code else ""
        print(f"  {rec.status}{code:<32} {name}")
        return rec

    def save_raw(self, name: str, payload: dict[str, Any]) -> None:
        write_json(self.out / f"{name}.json", payload)
        self.written.append(name)
        print(f"  ---{'':<32} {name}")

    def _safe_tick(self) -> int | None:
        try:
            return self.sim.tick()
        except Exception:  # noqa: BLE001 - tick is informational only
            return None


def alloc_body(key: str, depot: str, station: str, route: str, fuel: str, qty: float) -> dict:
    return {
        "idempotency_key": key,
        "source_depot_id": depot,
        "destination_station_id": station,
        "route_id": route,
        "fuel_type": fuel,
        "quantity": qty,
    }


def stream_fixture(cap: SSECapture, *, note: str, with_events: bool = False) -> dict[str, Any]:
    response: dict[str, Any] = {"status": cap.status, "headers": cap.headers}
    if cap.error_body is not None:
        try:
            response["json"] = json.loads(cap.error_body)
        except ValueError:
            response["text"] = cap.error_body
    if with_events:
        response["raw_lines"] = [line for _, line in cap.snapshot()]
        response["events"] = [{"event": e.event, "data": e.data} for e in cap.events()]
    return {
        "_meta": {
            "source": "recorded",
            "endpoint": "stream",
            "recorded_at": now_iso(),
            "simulator_image": SIMULATOR_IMAGE,
            "reader_ended": cap.ended,
            "note": note,
        },
        "request": {"method": "GET", "path": "/v1/stream", "params": None, "json": None},
        "response": response,
    }


def step_until(sim: Sim, predicate: Callable[[], bool], max_steps: int) -> int:
    """Step one tick at a time until predicate() holds; returns steps taken (-1 if never)."""
    for n in range(max_steps + 1):
        if predicate():
            return n
        if n < max_steps:
            sim.step(1)
    return -1


# -- sections -------------------------------------------------------------------


def section_reads(r: Recorder) -> None:
    sim = r.sim
    sim.clear_faults()
    r.save("admin_reset__ok", sim.post("/admin/reset"), "admin_reset")
    r.save("admin_pause__ok", sim.post("/admin/pause"), "admin_pause")

    r.save("health__ok", sim.get("/v1/health"), "health")
    r.save("instance__ok", sim.get("/v1/instance"), "instance")
    r.save("regions__ok", sim.get("/v1/regions"), "regions")
    r.save("depots__ok", sim.get("/v1/depots"), "depots")
    r.save("depot__ok", sim.get(f"/v1/depots/{GAZIPUR}"), "depot")
    r.save("depot__not_found", sim.get("/v1/depots/depot-nowhere"), "depot")
    r.save("stations__ok", sim.get("/v1/stations"), "stations")
    r.save("station__ok", sim.get(f"/v1/stations/{MIRPUR}"), "station")
    r.save("station__not_found", sim.get("/v1/stations/station-nowhere"), "station")
    r.save("routes__ok", sim.get("/v1/routes"), "routes")
    r.save("supply_arrivals__ok", sim.get("/v1/supply-arrivals"), "supply_arrivals")
    r.save("events__empty", sim.get("/v1/events"), "events")
    r.save("allocations__empty", sim.get("/v1/allocations"), "allocations")
    r.save(
        "demand_history__empty",
        sim.get("/v1/demand-history", station_id=MIRPUR, limit=50),
        "demand_history",
    )
    r.save("metrics__initial", sim.get("/v1/metrics"), "metrics")
    r.save("openapi__ok", sim.get("/openapi.json"), "openapi")
    r.save("admin_console__ok", sim.get("/admin"), "admin_console")
    r.save("admin_audit__initial", sim.get("/admin/audit", limit=20), "admin_audit")
    r.save("admin_faults__empty", sim.get("/admin/faults"), "admin_faults")
    r.save("admin_events__empty", sim.get("/admin/events"), "admin_events")

    r.save("admin_step__ok", sim.post("/admin/step"), "admin_step")
    sim.step(19)  # past the initial supply burst (ticks 12-20)
    r.save("instance__after_steps", sim.get("/v1/instance"), "instance")
    r.save(
        "demand_history__ok",
        sim.get("/v1/demand-history", station_id=MIRPUR, limit=24),
        "demand_history",
    )
    r.save(
        "demand_history__no_station_id",
        sim.get("/v1/demand-history", limit=5),
        "demand_history",
        note="station_id omitted: all stations, or 422?",
    )
    r.save(
        "demand_history__limit_zero",
        sim.get("/v1/demand-history", station_id=MIRPUR, limit=0),
        "demand_history",
        note="docs: limit clamped to [1, 2000] - clamp or 422?",
    )
    r.save(
        "demand_history__limit_over",
        sim.get("/v1/demand-history", station_id=MIRPUR, limit=5000),
        "demand_history",
        note="docs: limit clamped to [1, 2000] - clamp or 422?",
    )
    r.save(
        "demand_history__unknown_station",
        sim.get("/v1/demand-history", station_id="station-nowhere", limit=5),
        "demand_history",
    )
    r.save("metrics__ok", sim.get("/v1/metrics"), "metrics")
    r.save("supply_arrivals__after_burst", sim.get("/v1/supply-arrivals"), "supply_arrivals")
    r.save("depots__after_burst", sim.get("/v1/depots"), "depots")
    r.save("stations__after_steps", sim.get("/v1/stations"), "stations")
    r.save("admin_audit__ok", sim.get("/admin/audit", limit=50), "admin_audit")

    r.save("admin_run__ok", sim.post("/admin/run"), "admin_run")
    r.save("admin_toggle__ok", sim.post("/admin/toggle"), "admin_toggle")
    sim.pause()


def section_allocations(r: Recorder) -> None:
    sim = r.sim
    sim.reset()
    created_body = alloc_body("probe-created", GAZIPUR, MIRPUR, R_GAZ_MIR, "DIESEL", 3000)
    created = r.save(
        "create_allocation__created",
        sim.post("/v1/allocations", created_body),
        "create_allocation",
    )
    r.save(
        "create_allocation__replay",
        sim.post("/v1/allocations", created_body),
        "create_allocation",
        note="docs disagree: §5.4 says 201, cheat sheet says 200",
    )
    r.save(
        "create_allocation__key_mismatch",
        sim.post("/v1/allocations", {**created_body, "quantity": 3100}),
        "create_allocation",
    )
    r.save(
        "create_allocation__not_found",
        sim.post(
            "/v1/allocations",
            alloc_body("probe-404", "depot-nowhere", MIRPUR, R_GAZ_MIR, "DIESEL", 1000),
        ),
        "create_allocation",
    )
    r.save(
        "create_allocation__route_mismatch",
        sim.post(
            "/v1/allocations",
            alloc_body("probe-mismatch", GAZIPUR, TONGI, R_GAZ_MIR, "DIESEL", 1000),
        ),
        "create_allocation",
    )
    r.save(
        "create_allocation__route_capacity_exceeded",
        sim.post(
            "/v1/allocations",
            alloc_body("probe-route-cap", GAZIPUR, MIRPUR, R_GAZ_MIR, "DIESEL", 7001),
        ),
        "create_allocation",
    )
    r.save(
        "create_allocation__destination_capacity_exceeded",
        sim.post(
            "/v1/allocations",
            alloc_body("probe-dest-cap", GAZIPUR, MIRPUR, R_GAZ_MIR, "OCTANE", 5000),
        ),
        "create_allocation",
        note="station-mirpur OCTANE 5000/9000 at tick 0 -> room 4000",
    )
    sim.allocate("probe-dispatch-fill", GAZIPUR, TONGI, R_GAZ_TON, "DIESEL", 6500)
    r.save(
        "create_allocation__dispatch_capacity_exceeded",
        sim.post(
            "/v1/allocations",
            alloc_body("probe-dispatch-over", GAZIPUR, MIRPUR, R_GAZ_MIR, "DIESEL", 3000),
        ),
        "create_allocation",
        note="3000 + 6500 already pending from depot-gazipur this tick; +3000 > 12000",
    )
    r.save(
        "validation_error__quantity_zero",
        sim.post(
            "/v1/allocations",
            alloc_body("probe-422-qty", GAZIPUR, MIRPUR, R_GAZ_MIR, "DIESEL", 0),
        ),
        "create_allocation",
    )
    r.save(
        "validation_error__bad_fuel",
        sim.post(
            "/v1/allocations",
            alloc_body("probe-422-fuel", GAZIPUR, MIRPUR, R_GAZ_MIR, "KEROSENE", 1000),
        ),
        "create_allocation",
    )
    missing = alloc_body("probe-422-missing", GAZIPUR, MIRPUR, R_GAZ_MIR, "DIESEL", 1000)
    del missing["route_id"]
    r.save(
        "validation_error__missing_field",
        sim.post("/v1/allocations", missing),
        "create_allocation",
    )

    cancel_body = alloc_body("probe-cancel", PATIYA, KARNAPHULI, R_PAT_KAR, "PETROL", 2000)
    to_cancel = sim.post("/v1/allocations", cancel_body)
    if to_cancel.ok:
        cid = to_cancel.json["id"]
        r.save(
            "cancel_allocation__ok",
            sim.post(f"/v1/allocations/{cid}/cancel"),
            "cancel_allocation",
        )
        r.save(
            "cancel_allocation__already_cancelled",
            sim.post(f"/v1/allocations/{cid}/cancel"),
            "cancel_allocation",
        )
        r.save(
            "create_allocation__replay_after_cancel",
            sim.post("/v1/allocations", cancel_body),
            "create_allocation",
            note="cancellation does not free the key (docs §5.4)",
        )
    r.save(
        "cancel_allocation__not_found",
        sim.post("/v1/allocations/999999/cancel"),
        "cancel_allocation",
    )
    r.save("allocations__with_items", sim.get("/v1/allocations"), "allocations")

    if created.ok:
        aid = created.json["id"]

        def departed() -> bool:
            alloc = sim.allocation(aid)
            return alloc is not None and alloc["status"] != "PENDING"

        step_until(sim, departed, max_steps=3)
        r.save(
            "cancel_allocation__cannot_cancel",
            sim.post(f"/v1/allocations/{aid}/cancel"),
            "cancel_allocation",
        )
        r.save("allocations__in_transit", sim.get("/v1/allocations"), "allocations")

        def arrived() -> bool:
            alloc = sim.allocation(aid)
            return alloc is not None and alloc["status"] in {"ARRIVED", "FAILED"}

        step_until(sim, arrived, max_steps=6)
        r.save("allocations__ok", sim.get("/v1/allocations"), "allocations")
        r.save("metrics__after_allocations", sim.get("/v1/metrics"), "metrics")


def section_drain(r: Recorder) -> None:
    """Drain depot-gazipur OCTANE until INSUFFICIENT_INVENTORY can be provoked."""
    sim = r.sim
    sim.reset()
    fuel = "OCTANE"
    routes = [(R_GAZ_MIR, MIRPUR, 7000), (R_GAZ_TON, TONGI, 6500), (R_GAZ_KAR, KARNAPHULI, 5000)]
    n = 0
    for _ in range(24):
        for route, station, max_ship in routes:
            for _attempt in range(10):
                st = sim.station(station)
                room = st["capacity"][fuel] - st["inventory"][fuel]
                qty = min(max_ship, room)
                if qty <= 0:
                    break
                depot_inv = sim.depot(GAZIPUR)["inventory"][fuel]
                rec = sim.allocate(f"probe-drain-{n}", GAZIPUR, station, route, fuel, qty)
                n += 1
                if depot_inv < qty:
                    r.save(
                        "create_allocation__insufficient_inventory",
                        rec,
                        "create_allocation",
                        note=f"depot-gazipur OCTANE={depot_inv} < quantity={qty} after draining",
                    )
                    return
                if not rec.ok:
                    break  # dispatch cap for this tick, or destination full
        sim.step(1)
    print("  !! could not provoke INSUFFICIENT_INVENTORY within 24 ticks", file=sys.stderr)


def section_events(r: Recorder) -> None:
    sim = r.sim
    sim.reset()
    t = sim.tick()
    outage = r.save(
        "admin_events_create__station_outage",
        sim.inject_event("station_outage", t, 4, station_ids=[COXSBAZAR]),
        "admin_create_event",
    )
    r.save(
        "admin_events_create__route_disruption",
        sim.inject_event("route_disruption", t, 4, route_ids=[R_PAT_KAR]),
        "admin_create_event",
    )
    r.save(
        "admin_events_create__depot_constraint",
        sim.inject_event("depot_constraint", t, 4, depot_ids=[PATIYA]),
        "admin_create_event",
    )
    r.save(
        "admin_events_create__demand_spike",
        sim.inject_event(
            "demand_spike", t + 40, 8, region_ids=["region-chattogram"], multiplier=1.5
        ),
        "admin_create_event",
    )
    r.save(
        "admin_events_create__shipment_delay",
        sim.inject_event(
            "shipment_delay", t, 1, depot_ids=[GAZIPUR], fuel_types=["DIESEL"], delay_ticks=2
        ),
        "admin_create_event",
    )
    r.save(
        "admin_events_create__supply_shortfall",
        sim.inject_event(
            "supply_shortfall", t, 1, depot_ids=[PATIYA], fuel_types=["OCTANE"], factor=0.5
        ),
        "admin_create_event",
    )
    r.save(
        "admin_events__validation_error",
        sim.inject_event("station_outage", t, 0, station_ids=[COXSBAZAR]),
        "admin_create_event",
        note="duration_ticks=0",
    )
    r.save(
        "admin_events__bad_type",
        sim.inject_event("meteor_strike", t, 2),
        "admin_create_event",
    )
    r.save("events__right_after_inject", sim.get("/v1/events"), "events")

    if outage.ok:
        oid = outage.json["id"]

        def outage_active() -> bool:
            ev = sim.event(oid)
            return ev is not None and ev["status"] == "ACTIVE"

        steps = step_until(sim, outage_active, max_steps=3)
        note = f"stepped {steps} tick(s) after injecting with start_tick=current to reach ACTIVE"
        r.save("events__active_mixed", sim.get("/v1/events"), "events", note=note)
    r.save("stations__with_outage", sim.get("/v1/stations"), "stations")
    r.save("station__outage", sim.get(f"/v1/stations/{COXSBAZAR}"), "station")
    r.save("routes__with_disruption", sim.get("/v1/routes"), "routes")
    r.save("depots__with_constrained", sim.get("/v1/depots"), "depots")
    r.save(
        "supply_arrivals__after_delay_shortfall",
        sim.get("/v1/supply-arrivals"),
        "supply_arrivals",
    )
    r.save(
        "create_allocation__station_closed",
        sim.post(
            "/v1/allocations",
            alloc_body("probe-station-closed", PATIYA, COXSBAZAR, R_PAT_COX, "DIESEL", 1000),
        ),
        "create_allocation",
    )
    r.save(
        "create_allocation__route_disrupted",
        sim.post(
            "/v1/allocations",
            alloc_body("probe-route-disrupted", PATIYA, KARNAPHULI, R_PAT_KAR, "DIESEL", 1000),
        ),
        "create_allocation",
    )
    r.save(
        "create_allocation__from_constrained_depot",
        sim.post(
            "/v1/allocations",
            alloc_body("probe-constrained", PATIYA, MIRPUR, R_PAT_MIR, "DIESEL", 1000),
        ),
        "create_allocation",
        note="depot-patiya is CONSTRAINED: docs say still shippable",
    )
    r.save("admin_events__list", sim.get("/admin/events"), "admin_events")
    sim.step(6)
    r.save("events__ok", sim.get("/v1/events"), "events")


def section_faults(r: Recorder) -> None:
    sim = r.sim
    sim.reset()
    for type_, params in FAULTS:
        print(f"  -- fault {type_}")
        r.save(
            f"admin_faults_create__{type_}",
            sim.inject_fault(type_, 60, **params),
            "admin_create_fault",
        )
        if type_ == "error_rate":
            first_503: Rec | None = None
            first_200: Rec | None = None
            count_503 = 0
            for _ in range(20):
                rec = sim.get("/v1/instance")
                count_503 += rec.status == 503
                if rec.status == 503 and first_503 is None:
                    first_503 = rec
                if rec.ok and first_200 is None:
                    first_200 = rec
            note = f"{count_503}/20 requests returned 503 at rate=0.5"
            if first_503:
                r.save("fault_error_rate__instance_503", first_503, "instance", note=note)
            if first_200:
                r.save("fault_error_rate__instance_200", first_200, "instance", note=note)
        else:
            r.save(f"fault_{type_}__instance", sim.get("/v1/instance"), "instance")
        r.save(f"fault_{type_}__health", sim.get("/v1/health"), "health")
        r.save(f"fault_{type_}__admin", sim.get("/admin/faults"), "admin_faults")
        r.save(
            f"fault_{type_}__create_allocation",
            sim.post(
                "/v1/allocations",
                alloc_body(f"probe-fault-{type_}", GAZIPUR, TONGI, R_GAZ_MIR, "DIESEL", 1000),
            ),
            "create_allocation",
            note="body is a deliberate ROUTE_MISMATCH: 409 = fault did not apply to writes",
        )
        cap = SSECapture(base_url=sim.base_url, read_timeout=5.0).start(wait_s=5.0)
        cap.stop(grace_s=0.2)
        r.save_raw(
            f"fault_{type_}__stream",
            stream_fixture(cap, note=f"GET /v1/stream while {type_} fault active"),
        )
        r.save("admin_faults_clear__ok", sim.clear_faults(), "admin_clear_faults")

    r.save(
        "admin_faults__bad_type",
        sim.inject_fault("meteor_strike", 10),
        "admin_create_fault",
    )
    r.save(
        "admin_faults__validation_error",
        sim.inject_fault("latency", 0),
        "admin_create_fault",
        note="duration_seconds=0",
    )
    r.save("admin_faults__list", sim.get("/admin/faults"), "admin_faults")


def section_stream(r: Recorder) -> None:
    sim = r.sim
    sim.reset()
    cap = SSECapture(base_url=sim.base_url).start()
    sim.allocate("probe-sse-1", GAZIPUR, MIRPUR, R_GAZ_MIR, "DIESEL", 2000)
    cancel_me = sim.allocate("probe-sse-2", GAZIPUR, TONGI, R_GAZ_TON, "PETROL", 1000)
    if cancel_me.ok:
        sim.post(f"/v1/allocations/{cancel_me.json['id']}/cancel")
    sim.step(4)
    sim.post("/admin/reset")
    sim.pause()
    cap.stop(grace_s=1.0)
    r.save_raw(
        "stream__sample",
        stream_fixture(
            cap,
            note="create 2 allocations, cancel 1, step 4 ticks, then /admin/reset",
            with_events=True,
        ),
    )
    seen: set[str] = set()
    for ev in cap.events():
        if ev.event in seen:
            continue
        seen.add(ev.event)
        r.save_raw(
            f"stream_event__{ev.event.replace('.', '_')}",
            {
                "_meta": {
                    "source": "recorded",
                    "endpoint": "stream_event",
                    "recorded_at": now_iso(),
                    "simulator_image": SIMULATOR_IMAGE,
                },
                "event": ev.event,
                "data": ev.data,
            },
        )


SECTIONS: dict[str, Callable[[Recorder], None]] = {
    "reads": section_reads,
    "allocations": section_allocations,
    "drain": section_drain,
    "events": section_events,
    "faults": section_faults,
    "stream": section_stream,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--out", type=Path, default=FIXTURES_DIR)
    parser.add_argument(
        "--only", default=",".join(SECTIONS), help=f"comma list of: {', '.join(SECTIONS)}"
    )
    args = parser.parse_args()

    chosen = [s.strip() for s in args.only.split(",") if s.strip()]
    unknown = sorted(set(chosen) - set(SECTIONS))
    if unknown:
        parser.error(f"unknown section(s): {', '.join(unknown)}")

    sim = Sim(args.base_url)
    health = sim.wait_healthy()
    print(f"simulator {args.base_url} healthy: {health}")
    rec = Recorder(sim, args.out)
    failures: list[str] = []
    started = time.monotonic()
    try:
        for name in chosen:
            print(f"== {name}")
            try:
                SECTIONS[name](rec)
            except Exception as exc:  # noqa: BLE001 - keep probing other sections
                failures.append(f"{name}: {type(exc).__name__}: {exc}")
                print(f"  !! section {name} failed: {exc}", file=sys.stderr)
    finally:
        sim.reset()  # leave a clean, paused world with no active faults
        sim.close()

    print(
        f"\n{len(rec.written)} fixtures written to {args.out} in {time.monotonic() - started:.1f}s"
    )
    for failure in failures:
        print(f"FAILED {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
