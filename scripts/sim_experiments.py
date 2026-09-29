#!/usr/bin/env python3
"""Phase 0.5 deterministic experiments against a live BUP Fuel Supply Simulator.

Every experiment starts from /admin/reset + /admin/pause and drives time with
/admin/step, so results are reproducible (same image + seed => same numbers).
Results go to docs/experiments/phase0_results.json (+ raw series under raw/);
docs/SIMULATOR_NOTES.md is written from them.

WARNING: this RESETS the simulator it points at many times.

    python scripts/sim_experiments.py                  # all fast experiments
    python scripts/sim_experiments.py --slow           # + keepalive / slow-consumer (~1.5 min)
    python scripts/sim_experiments.py --only e2,e5
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import traceback
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

import httpx

from simlib import (
    DEFAULT_BASE_URL,
    REPO_ROOT,
    SIMULATOR_IMAGE,
    Sim,
    SSECapture,
    now_iso,
    percentile,
    write_json,
)

OUT_DIR = REPO_ROOT / "docs" / "experiments"

GAZIPUR, PATIYA = "depot-gazipur", "depot-patiya"
MIRPUR, TONGI = "station-mirpur", "station-tongi"
KARNAPHULI, COXSBAZAR = "station-karnaphuli", "station-coxsbazar"
R_GAZ_MIR, R_GAZ_TON = "route-gazipur-mirpur", "route-gazipur-tongi"
R_PAT_COX = "route-patiya-coxsbazar"
FUELS = ("DIESEL", "PETROL", "OCTANE")

# Integration guide §8.5 / §8.6
PROFILE_DAILY = {
    "urban_high": {"DIESEL": 8500, "PETROL": 10500, "OCTANE": 5600},
    "industrial": {"DIESEL": 14000, "PETROL": 4500, "OCTANE": 2200},
    "highway": {"DIESEL": 10500, "PETROL": 11000, "OCTANE": 6200},
    "regional": {"DIESEL": 7200, "PETROL": 7600, "OCTANE": 3600},
}
PROFILE_NOISE = {"urban_high": 0.10, "industrial": 0.08, "highway": 0.12, "regional": 0.10}


def doc_hour_factor(profile: str, hour: int) -> float:
    if profile == "industrial":
        return 1.55 if 6 <= hour <= 17 else 0.45
    if profile == "highway":
        return 1.35 if (6 <= hour <= 9 or 16 <= hour <= 20) else 0.75
    if profile == "urban_high":
        return 1.45 if (7 <= hour <= 9 or 16 <= hour <= 20) else 0.70
    if profile == "regional":
        return 1.25 if 7 <= hour <= 20 else 0.65
    raise KeyError(profile)


def doc_mean_factor(profile: str) -> float:
    return statistics.fmean(doc_hour_factor(profile, h) for h in range(24))


def r4(x: float | None) -> float | None:
    return None if x is None else round(x, 4)


def hour_of(row: dict[str, Any]) -> int:
    return datetime.fromisoformat(row["sim_time"]).hour


Rows = dict[tuple[str, str, int], dict[str, Any]]


def fetch_rows(sim: Sim, station_ids: list[str]) -> Rows:
    rows: Rows = {}
    for sid in station_ids:
        for row in sim.demand_rows(sid, limit=2000):
            rows[(row["station_id"], row["fuel_type"], row["tick"])] = row
    return rows


def fill_dispatch(sim: Sim, prefix: str, depot: str, station: str, route: str) -> dict[str, Any]:
    """POST 1000 L DIESEL shipments until the simulator refuses; returns accepted total."""
    accepted = 0
    stop_code = None
    for i in range(20):
        rec = sim.allocate(f"{prefix}-{i}", depot, station, route, "DIESEL", 1000)
        if not rec.ok:
            stop_code = rec.code
            break
        accepted += 1000
    return {"accepted_liters": accepted, "stopped_with": stop_code}


# -- E1: demand, hour factors, alignment, independence, determinism -----------------


def e1_demand(sim: Sim, slow: bool) -> dict[str, Any]:
    sim.reset()
    regions = {r["id"]: r for r in sim.get_json("/v1/regions")}
    stations = {s["id"]: s for s in sim.get_json("/v1/stations")}
    sids = sorted(stations)

    def snap() -> tuple[dict[str, Any], dict[str, Any]]:
        st = {s["id"]: s["inventory"] for s in sim.get_json("/v1/stations")}
        dp = {d["id"]: d["inventory"] for d in sim.get_json("/v1/depots")}
        return st, dp

    st_traj, dp_traj = [], []
    st0, dp0 = snap()
    st_traj.append(st0)
    dp_traj.append(dp0)
    for _ in range(96):
        sim.step(1)
        st, dp = snap()
        st_traj.append(st)
        dp_traj.append(dp)
    rows = fetch_rows(sim, sids)
    arrivals = sim.get_json("/v1/supply-arrivals")
    depots = {d["id"]: d for d in sim.get_json("/v1/depots")}

    all_ticks = sorted({k[2] for k in rows})
    day = set(all_ticks[:96])
    result: dict[str, Any] = {
        "row_ticks": {
            "min": all_ticks[0] if all_ticks else None,
            "max": all_ticks[-1] if all_ticks else None,
            "count": len(all_ticks),
            "rows_total": len(rows),
        },
    }

    # Daily totals vs documented profile
    daily: dict[str, Any] = {}
    for sid in sids:
        s = stations[sid]
        prof = s["demand_profile"]
        rf = regions[s["region_id"]]["demand_factor"]
        for fuel in FUELS:
            total = sum(
                r["demand_liters"]
                for (ssid, f, t), r in rows.items()
                if ssid == sid and f == fuel and t in day
            )
            expected = PROFILE_DAILY[prof][fuel] * rf * s["demand_multiplier"]
            daily[f"{sid}/{fuel}"] = {
                "profile": prof,
                "total_liters": r4(total),
                "profile_x_region_liters": r4(expected),
                "ratio": r4(total / expected) if expected else None,
                "ratio_if_factors_unnormalized": r4(doc_mean_factor(prof)),
            }
    result["daily_totals"] = daily
    system: dict[str, float] = defaultdict(float)
    for (_, fuel, t), r in rows.items():
        if t in day:
            system[fuel] += r["demand_liters"]
    result["system_daily_liters"] = {k: r4(v) for k, v in system.items()}

    # Hour-of-day factor estimates + noise, raw and normalized readings of the doc table
    per_profile: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    resid_raw: dict[str, list[float]] = defaultdict(list)
    resid_norm: dict[str, list[float]] = defaultdict(list)
    for (sid, fuel, t), r in rows.items():
        if t not in day:
            continue
        s = stations[sid]
        prof = s["demand_profile"]
        base = PROFILE_DAILY[prof][fuel] * regions[s["region_id"]]["demand_factor"] / 96
        est = r["demand_liters"] / base
        h = hour_of(r)
        per_profile[prof][h].append(est)
        f_raw = doc_hour_factor(prof, h)
        resid_raw[prof].append(est / f_raw - 1)
        resid_norm[prof].append(est / (f_raw / doc_mean_factor(prof)) - 1)
    hour_factors: dict[str, Any] = {}
    for prof, by_hour in per_profile.items():
        est_by_hour = {h: r4(statistics.fmean(v)) for h, v in sorted(by_hour.items())}
        hour_factors[prof] = {
            "estimated_by_hour": est_by_hour,
            "doc_by_hour": {h: doc_hour_factor(prof, h) for h in range(24)},
            "doc_mean_factor": r4(doc_mean_factor(prof)),
            "mean_residual_vs_raw_doc": r4(statistics.fmean(resid_raw[prof])),
            "mean_residual_vs_normalized_doc": r4(statistics.fmean(resid_norm[prof])),
            "residual_std_vs_raw_doc": r4(statistics.pstdev(resid_raw[prof])),
            "doc_noise": PROFILE_NOISE[prof],
        }
    result["hour_factors"] = hour_factors
    discriminating = [p for p in ("urban_high", "highway") if p in hour_factors]
    if discriminating:
        raw_err = statistics.fmean(
            abs(hour_factors[p]["mean_residual_vs_raw_doc"]) for p in discriminating
        )
        norm_err = statistics.fmean(
            abs(hour_factors[p]["mean_residual_vs_normalized_doc"]) for p in discriminating
        )
        result["verdict_hour_factors"] = (
            "NOT normalized: daily total = profile x mean(hour factor)"
            if raw_err < norm_err
            else "normalized: daily total = profile"
        )

    # Which demand-row tick does an inventory drop belong to?
    offsets: Counter[int] = Counter()
    align_err: dict[str, Any] = {}
    for sid in sids:
        for fuel in FUELS:
            drops = {T: st_traj[T - 1][sid][fuel] - st_traj[T][sid][fuel] for T in range(1, 97)}
            best = None
            for k in (-1, 0, 1):
                pairs = [
                    (d, rows[(sid, fuel, T + k)]["served_liters"])
                    for T, d in drops.items()
                    if (sid, fuel, T + k) in rows
                ]
                if not pairs:
                    continue
                err = sum(abs(d - s) for d, s in pairs) / len(pairs)
                if best is None or err < best[1]:
                    best = (k, err)
            if best:
                offsets[best[0]] += 1
                align_err[f"{sid}/{fuel}"] = {"offset": best[0], "mean_abs_err": r4(best[1])}
    result["alignment"] = {
        "meaning": (
            "served_liters of row(tick=T+offset) == inventory(state T-1) - inventory(state T), "
            "where state T is what GETs return after /admin/step returned tick T"
        ),
        "offset_votes": dict(offsets),
        "per_series": align_err,
    }

    # Stockouts with no action
    first_unmet: dict[str, int | None] = {}
    for sid in sids:
        for fuel in FUELS:
            ticks = sorted(
                t
                for (s2, f, t), r in rows.items()
                if s2 == sid and f == fuel and r["unmet_liters"] > 0
            )
            first_unmet[f"{sid}/{fuel}"] = ticks[0] if ticks else None
    result["first_unmet_tick_no_action"] = first_unmet

    # Supply arrivals: does depot inventory jump by exactly `quantity`, on which state?
    arr_checks = []
    for a in arrivals:
        if a["status"] != "ARRIVED" or a.get("actual_tick") is None:
            continue
        at = a["actual_tick"]
        entry = {
            "id": a["id"],
            "depot": a["depot_id"],
            "fuel": a["fuel_type"],
            "quantity": a["quantity"],
            "planned_tick": a["planned_tick"],
            "actual_tick": at,
        }
        for off in (0, 1):
            T = at + off
            if 1 <= T <= 96:
                entry[f"delta_state_{'T' if off == 0 else 'T+1'}"] = r4(
                    dp_traj[T][a["depot_id"]][a["fuel_type"]]
                    - dp_traj[T - 1][a["depot_id"]][a["fuel_type"]]
                )
        entry["depot_capacity"] = depots[a["depot_id"]]["capacity"][a["fuel_type"]]
        arr_checks.append(entry)
    result["supply_arrivals_applied"] = arr_checks
    result["supply_schedule"] = [
        {k: a.get(k) for k in ("id", "depot_id", "fuel_type", "quantity", "planned_tick", "status")}
        for a in arrivals
    ]

    write_json(
        OUT_DIR / "raw" / "e1_demand_rows.json", sorted(rows.values(), key=lambda r: r["id"])
    )
    write_json(OUT_DIR / "raw" / "e1_trajectories.json", {"stations": st_traj, "depots": dp_traj})

    # Independence: same seed, different actions -> identical demand?
    sim.reset()
    sim.allocate("e1b-1", GAZIPUR, MIRPUR, R_GAZ_MIR, "DIESEL", 5000)
    sim.allocate("e1b-2", PATIYA, COXSBAZAR, R_PAT_COX, "PETROL", 4000)
    sim.allocate("e1b-3", GAZIPUR, TONGI, R_GAZ_TON, "OCTANE", 2000)
    sim.step(96)
    rows_b = fetch_rows(sim, sids)
    common = set(rows) & set(rows_b)
    d_diff = [abs(rows[k]["demand_liters"] - rows_b[k]["demand_liters"]) for k in common]
    s_diff = [abs(rows[k]["served_liters"] - rows_b[k]["served_liters"]) for k in common]
    result["independence"] = {
        "common_rows": len(common),
        "demand_rows_differing": sum(d > 1e-9 for d in d_diff),
        "demand_max_abs_diff": r4(max(d_diff, default=0.0)),
        "served_rows_differing": sum(d > 1e-9 for d in s_diff),
        "verdict": "demand independent of our actions"
        if d_diff and max(d_diff) < 1e-9
        else "demand CHANGED with our actions",
    }

    # Determinism: same seed, same (no) actions -> identical?
    sim.reset()
    sim.step(24)
    rows_c = fetch_rows(sim, sids)
    common_c = set(rows) & set(rows_c)
    c_diff = [abs(rows[k]["demand_liters"] - rows_c[k]["demand_liters"]) for k in common_c]
    result["determinism"] = {
        "common_rows": len(common_c),
        "demand_max_abs_diff": r4(max(c_diff, default=0.0)),
        "verdict": "deterministic" if c_diff and max(c_diff) < 1e-9 else "NOT deterministic",
    }
    return result


# -- E2: idempotency --------------------------------------------------------------


def e2_idempotency(sim: Sim, slow: bool) -> dict[str, Any]:
    sim.reset()
    body = ("e2-a", GAZIPUR, MIRPUR, R_GAZ_MIR, "DIESEL", 3000)
    first = sim.allocate(*body)
    replay = sim.allocate(*body)
    float_replay = sim.allocate(*body[:5], 3000.0)
    mismatch = sim.allocate(*body[:5], 3100)
    sim.step(1)
    replay_after_step = sim.allocate(*body)
    cancel_me = sim.allocate("e2-b", PATIYA, KARNAPHULI, "route-patiya-karnaphuli", "PETROL", 1000)
    cancel = sim.post(f"/v1/allocations/{cancel_me.json['id']}/cancel") if cancel_me.ok else None
    replay_cancelled = sim.allocate(
        "e2-b", PATIYA, KARNAPHULI, "route-patiya-karnaphuli", "PETROL", 1000
    )
    reuse_cancelled = sim.allocate(
        "e2-b", PATIYA, KARNAPHULI, "route-patiya-karnaphuli", "PETROL", 1500
    )
    long_key = sim.allocate("k" * 151, GAZIPUR, MIRPUR, R_GAZ_MIR, "DIESEL", 100)

    def brief(rec: Any) -> dict[str, Any] | None:
        if rec is None:
            return None
        body_ = rec.json if isinstance(rec.json, dict) else {}
        return {
            "status": rec.status,
            "code": rec.code,
            "id": body_.get("id"),
            "allocation_status": body_.get("status"),
        }

    return {
        "create": brief(first),
        "replay_same_body": brief(replay),
        "replay_same_body_same_id": bool(
            first.ok and replay.ok and first.json["id"] == replay.json["id"]
        ),
        "replay_quantity_as_float": brief(float_replay),
        "same_key_different_body": brief(mismatch),
        "replay_after_step": brief(replay_after_step),
        "cancel": brief(cancel),
        "replay_cancelled": brief(replay_cancelled),
        "reuse_cancelled_key_new_body": brief(reuse_cancelled),
        "key_151_chars": brief(long_key),
    }


# -- E3: overflow at arrival ----------------------------------------------------------


def e3_overflow(sim: Sim, slow: bool) -> dict[str, Any]:
    sim.reset()
    fuel = "OCTANE"
    st = sim.station(TONGI)
    cap, inv0 = st["capacity"][fuel], st["inventory"][fuel]
    room = cap - inv0
    depot_before = sim.depot(GAZIPUR)["inventory"][fuel]
    a = sim.allocate("e3-a", GAZIPUR, TONGI, R_GAZ_TON, fuel, room)
    depot_after_a = sim.depot(GAZIPUR)["inventory"][fuel]
    b = sim.allocate("e3-b", GAZIPUR, TONGI, R_GAZ_TON, fuel, room)
    ids = [x.json["id"] for x in (a, b) if x.ok]
    timeline = []
    for _ in range(6):
        tick = sim.step(1)
        allocs = {x["id"]: x for x in sim.get_json("/v1/allocations") if x["id"] in ids}
        timeline.append(
            {
                "tick": tick,
                "station_inventory": sim.station(TONGI)["inventory"][fuel],
                "depot_inventory": sim.depot(GAZIPUR)["inventory"][fuel],
                "allocations": {i: allocs[i]["status"] for i in allocs},
            }
        )
    rows = [r for r in sim.demand_rows(TONGI, limit=200) if r["fuel_type"] == fuel]
    served = sum(r["served_liters"] for r in rows)
    final_inv = timeline[-1]["station_inventory"]
    uncapped = inv0 - served + room * len(ids)
    audit = [
        e
        for e in sim.get_json("/admin/audit", limit=200)
        if e.get("entity_type") == "allocation" and str(e.get("entity_id")) in {str(i) for i in ids}
    ]
    if final_inv > cap + 1e-6:
        verdict = "station inventory EXCEEDS capacity after arrival (no cap)"
    elif abs(final_inv - cap) < 1.0 or final_inv < uncapped - 1.0:
        verdict = "arrival is CLAMPED to capacity (excess lost)"
    else:
        verdict = "inconclusive"
    return {
        "station": TONGI,
        "fuel": fuel,
        "capacity": cap,
        "initial_inventory": inv0,
        "shipment_each": room,
        "second_allocation_accepted": b.ok,
        "second_allocation_code": b.code,
        "depot_inventory_before": depot_before,
        "depot_inventory_after_create_pending": depot_after_a,
        "depot_deducted_at_creation": depot_after_a < depot_before,
        "timeline": timeline,
        "served_liters_in_window": r4(served),
        "final_inventory": final_inv,
        "final_if_uncapped": r4(uncapped),
        "audit_entries": audit,
        "verdict": verdict,
    }


# -- E4: dispatch capacity, OPEN vs CONSTRAINED ----------------------------------------------


def e4_dispatch(sim: Sim, slow: bool) -> dict[str, Any]:
    sim.reset()
    baseline_depot = sim.depot(GAZIPUR)
    t0 = fill_dispatch(sim, "e4-t0", GAZIPUR, MIRPUR, R_GAZ_MIR)
    sim.step(1)
    statuses_t1 = Counter(a["status"] for a in sim.get_json("/v1/allocations"))
    t1 = fill_dispatch(sim, "e4-t1", GAZIPUR, MIRPUR, R_GAZ_MIR)
    sim.step(1)
    t2 = fill_dispatch(sim, "e4-t2", GAZIPUR, MIRPUR, R_GAZ_MIR)

    sim.reset()
    ev = sim.inject_event("depot_constraint", 0, 20, depot_ids=[GAZIPUR])
    depot_immediately = sim.depot(GAZIPUR)
    steps = 0
    while sim.depot(GAZIPUR)["status"] != "CONSTRAINED" and steps < 3:
        sim.step(1)
        steps += 1
    constrained_depot = sim.depot(GAZIPUR)
    constrained = fill_dispatch(sim, "e4-c", GAZIPUR, MIRPUR, R_GAZ_MIR)
    reduced = constrained["accepted_liters"] < t0["accepted_liters"]
    return {
        "baseline_depot": baseline_depot,
        "open_tick0": t0,
        "allocation_statuses_after_1_step": dict(statuses_t1),
        "open_tick1_with_prior_in_flight": t1,
        "open_tick2_with_prior_in_flight": t2,
        "in_flight_from_earlier_ticks_counts": t1["accepted_liters"] < t0["accepted_liters"],
        "constraint_event": ev.json,
        "depot_right_after_inject": depot_immediately,
        "steps_until_constrained": steps,
        "constrained_depot": constrained_depot,
        "constrained_fill": constrained,
        "verdict": (
            "CONSTRAINED reduces effective dispatch capacity"
            if reduced
            else "CONSTRAINED is only a signal (dispatch unchanged)"
        ),
    }


# -- E5: route disruption vs pending/in-transit; cancel refund ----------------------------------


def e5_disruption(sim: Sim, slow: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}

    # a) disruption injected with start_tick = now, allocations PENDING
    sim.reset()
    t0 = sim.tick()
    d0 = sim.depot(GAZIPUR)["inventory"]
    a = sim.allocate("e5-a", GAZIPUR, MIRPUR, R_GAZ_MIR, "DIESEL", 3000)
    b = sim.allocate("e5-b", GAZIPUR, MIRPUR, R_GAZ_MIR, "PETROL", 2000)
    d1 = sim.depot(GAZIPUR)["inventory"]
    ev = sim.inject_event("route_disruption", t0, 4, route_ids=[R_GAZ_MIR])
    route_now = sim.route(R_GAZ_MIR)["status"]
    ev_now = sim.event(ev.json["id"]) if ev.ok else None
    c = sim.allocate("e5-c", GAZIPUR, MIRPUR, R_GAZ_MIR, "DIESEL", 1000)
    cancel = sim.post(f"/v1/allocations/{b.json['id']}/cancel") if b.ok else None
    d2 = sim.depot(GAZIPUR)["inventory"]
    sim.step(1)
    a_after = sim.allocation(a.json["id"]) if a.ok else None
    d3 = sim.depot(GAZIPUR)["inventory"]
    metrics = sim.get_json("/v1/metrics")
    timeline = []
    for _ in range(8):
        tick = sim.step(1)
        timeline.append(
            {
                "tick": tick,
                "route": sim.route(R_GAZ_MIR)["status"],
                "event": (sim.event(ev.json["id"]) or {}).get("status") if ev.ok else None,
            }
        )
    out["a_inject_now_pending"] = {
        "injected_at_tick": t0,
        "event": ev.json,
        "route_status_immediately": route_now,
        "event_status_immediately": (ev_now or {}).get("status"),
        "post_on_route_immediately": {"status": c.status, "code": c.code},
        "depot_before": d0,
        "depot_after_create": d1,
        "cancel": {"status": cancel.status, "allocation_status": (cancel.json or {}).get("status")}
        if cancel
        else None,
        "depot_after_cancel": d2,
        "petrol_refunded_liters": d2["PETROL"] - d1["PETROL"],
        "pending_after_1_step": a_after,
        "depot_after_failure": d3,
        "diesel_refunded_on_failure_liters": d3["DIESEL"] - d2["DIESEL"],
        "metrics_after": metrics,
        "route_timeline": timeline,
    }

    # b) disruption scheduled to start exactly at the departure tick
    sim.reset()
    t0 = sim.tick()
    d = sim.allocate("e5-d", GAZIPUR, MIRPUR, R_GAZ_MIR, "DIESEL", 3000)
    sim.inject_event("route_disruption", t0 + 1, 3, route_ids=[R_GAZ_MIR])
    sim.step(1)
    out["b_disruption_starts_at_departure_tick"] = {
        "allocation_after_1_step": sim.allocation(d.json["id"]) if d.ok else None,
        "route_after_1_step": sim.route(R_GAZ_MIR)["status"],
    }

    # c) disruption starts while the shipment is already IN_TRANSIT
    sim.reset()
    e = sim.allocate("e5-e", GAZIPUR, MIRPUR, R_GAZ_MIR, "DIESEL", 3000)
    sim.step(1)
    before = sim.allocation(e.json["id"]) if e.ok else None
    sim.inject_event("route_disruption", sim.tick(), 4, route_ids=[R_GAZ_MIR])
    statuses = []
    for _ in range(5):
        tick = sim.step(1)
        now = sim.allocation(e.json["id"]) if e.ok else None
        statuses.append(
            {
                "tick": tick,
                "status": (now or {}).get("status"),
                "route": sim.route(R_GAZ_MIR)["status"],
            }
        )
    out["c_disruption_while_in_transit"] = {"before": before, "timeline": statuses}
    return out


# -- E6: faults -------------------------------------------------------------------


FAULT_PARAMS: list[tuple[str, dict[str, Any]]] = [
    ("latency", {"delay_ms": 400}),
    ("unavailable", {}),
    ("error_rate", {"rate": 0.3}),
    ("stale_data", {}),
    ("stream_disconnect", {}),
]


def _mismatch_body(key: str) -> dict[str, Any]:
    return {
        "idempotency_key": key,
        "source_depot_id": GAZIPUR,
        "destination_station_id": TONGI,
        "route_id": R_GAZ_MIR,
        "fuel_type": "DIESEL",
        "quantity": 1000,
    }


def _probe_paths(sim: Sim, label: str, n_instance: int) -> dict[str, Any]:
    inst = [sim.get("/v1/instance") for _ in range(n_instance)]
    health = sim.get("/v1/health")
    admin = sim.get("/admin/faults")
    post = sim.post("/v1/allocations", _mismatch_body(f"e6-{label}-{time.monotonic_ns()}"))
    cap = SSECapture(base_url=sim.base_url, read_timeout=5.0).start(wait_s=6.0)
    cap.stop(grace_s=0.1)

    def summary(recs: list[Any]) -> dict[str, Any]:
        return {
            "n": len(recs),
            "statuses": dict(Counter(r.status for r in recs)),
            "codes": dict(Counter(r.code for r in recs if r.code)),
            "stale_header": dict(
                Counter(r.headers.get("x-simulator-stale", "absent") for r in recs)
            ),
            "elapsed_ms_mean": r4(statistics.fmean(r.elapsed_ms for r in recs)),
        }

    return {
        "GET /v1/instance": summary(inst),
        "GET /v1/health": summary([health]),
        "GET /admin/faults": summary([admin]),
        "POST /v1/allocations (ROUTE_MISMATCH body)": summary([post]),
        "GET /v1/stream": {
            "status": cap.status,
            "stale_header": cap.headers.get("x-simulator-stale", "absent"),
            "error_body": cap.error_body,
        },
        "error_body_example": next((r.json for r in inst if r.status >= 500), None),
    }


def e6_faults(sim: Sim, slow: bool) -> dict[str, Any]:
    sim.reset()
    out: dict[str, Any] = {"no_fault_baseline": _probe_paths(sim, "baseline", 5)}
    for type_, params in FAULT_PARAMS:
        created = sim.inject_fault(type_, 30, **params)
        n = 100 if type_ == "error_rate" else 5
        out[type_] = {
            "create": {"status": created.status, "body": created.json},
            "params": params,
            "effects": _probe_paths(sim, type_, n),
        }
        sim.clear_faults()

    for type_ in ("latency", "error_rate"):
        sim.inject_fault(type_, 30)
        recs = [sim.get("/v1/instance") for _ in range(100 if type_ == "error_rate" else 5)]
        out[f"{type_}_default_params"] = {
            "statuses": dict(Counter(r.status for r in recs)),
            "elapsed_ms_mean": r4(statistics.fmean(r.elapsed_ms for r in recs)),
        }
        sim.clear_faults()

    sim.inject_fault("latency", 30, delay_ms=300)
    sim.inject_fault("stale_data", 30)
    combo = sim.get("/v1/instance")
    out["latency_plus_stale_combined"] = {
        "status": combo.status,
        "elapsed_ms": r4(combo.elapsed_ms),
        "stale_header": combo.headers.get("x-simulator-stale", "absent"),
    }
    sim.clear_faults()

    sim.inject_fault("unavailable", 2)
    t_start = time.monotonic()
    first = sim.get("/v1/instance").status
    recovered_after = None
    while time.monotonic() - t_start < 10:
        if sim.get("/v1/instance").ok:
            recovered_after = time.monotonic() - t_start
            break
        time.sleep(0.25)
    out["expiry_unavailable_2s"] = {"first_status": first, "recovered_after_s": r4(recovered_after)}

    # Does stream_disconnect kill an already-open stream?
    sim.clear_faults()
    cap = SSECapture(base_url=sim.base_url).start()
    sim.inject_fault("stream_disconnect", 30)
    tick_before = sim.tick()
    sim.step(3)
    cap.stop(grace_s=1.0)
    ticks_seen = [e.data.get("tick") for e in cap.events() if e.event == "simulation.tick"]
    out["stream_disconnect_on_open_stream"] = {
        "ticks_stepped_after_fault": [tick_before + i for i in (1, 2, 3)],
        "tick_events_received": ticks_seen,
        "reader_ended": cap.ended,
    }
    sim.clear_faults()
    out["clear"] = sim.clear_faults().json
    return out


# -- E7: SSE cadence, keepalive, slow consumer -----------------------------------------------


def e7_sse(sim: Sim, slow: bool) -> dict[str, Any]:
    sim.reset()
    sim.allocate("e7-1", GAZIPUR, MIRPUR, R_GAZ_MIR, "DIESEL", 3000)
    cap = SSECapture(base_url=sim.base_url).start()
    tick_before = sim.tick()
    sim.run()
    time.sleep(10.0)
    sim.pause()
    tick_after = sim.tick()
    cap.stop(grace_s=1.0)
    evs = cap.events()
    ticks = [e for e in evs if e.event == "simulation.tick"]
    numbers = [e.data["tick"] for e in ticks]
    gaps = [b.t - a.t for a, b in pairwise(ticks)]
    missing = sorted(set(range(min(numbers), max(numbers) + 1)) - set(numbers)) if numbers else []
    out: dict[str, Any] = {
        "run_seconds": 10.0,
        "ticks_advanced": tick_after - tick_before,
        "ticks_per_second": r4((tick_after - tick_before) / 10.0),
        "tick_events": len(ticks),
        "missing_tick_numbers": missing,
        "interval_s": {
            "mean": r4(statistics.fmean(gaps)) if gaps else None,
            "p50": r4(percentile(gaps, 0.5)),
            "p95": r4(percentile(gaps, 0.95)),
            "max": r4(max(gaps, default=0.0)),
        },
        "event_names": dict(Counter(e.event for e in evs)),
        "inventory_updated_entity_types": sorted(
            {str(e.data.get("entity_type")) for e in evs if e.event == "inventory.updated"}
        ),
        "comments": [line for _, line in cap.comments()][:5],
        "reader_ended": cap.ended,
    }
    if not slow:
        out["slow_tests"] = "skipped (use --slow)"
        return out

    sim.reset()
    cap = SSECapture(base_url=sim.base_url).start()
    time.sleep(17.0)
    cap.stop(grace_s=0.0)
    out["keepalive_while_paused"] = {"comments": [(r4(t), line) for t, line in cap.comments()]}

    # Slow consumer: connect, stop reading for 35 s at speed 8 (> 200 events), then read.
    sim.reset()
    lines: list[tuple[float, str]] = []
    ended = None
    produced = 0
    with (
        httpx.Client(base_url=sim.base_url, timeout=httpx.Timeout(10.0, read=20.0)) as c,
        c.stream("GET", "/v1/stream") as resp,
    ):
        it = resp.iter_lines()
        first = next(it, None)
        t_before = sim.tick()
        sim.run()
        time.sleep(35.0)
        sim.pause()
        produced = sim.tick() - t_before
        t_read = time.monotonic()
        try:
            for line in it:
                lines.append((time.monotonic() - t_read, line))
                if time.monotonic() - t_read > 25:
                    ended = "read window over (connection still alive)"
                    break
            else:
                ended = "server closed stream"
        except httpx.ReadTimeout:
            ended = "read timeout: no bytes for 20 s"
        except httpx.HTTPError as exc:
            ended = f"{type(exc).__name__}: {exc}"
    tick_lines = [line for _, line in lines if line.startswith("event: simulation.tick")]
    out["slow_consumer"] = {
        "first_line": first,
        "ticks_produced_while_not_reading": produced,
        "tick_events_read_afterwards": len(tick_lines),
        "comments_afterwards": [(r4(t), line) for t, line in lines if line.startswith(":")],
        "ended": ended,
    }
    return out


# -- E8: reset ----------------------------------------------------------------------------


def e8_reset(sim: Sim, slow: bool) -> dict[str, Any]:
    sim.reset()
    sim.step(10)
    sim.allocate("e8-1", GAZIPUR, MIRPUR, R_GAZ_MIR, "DIESEL", 2000)
    sim.inject_event("demand_spike", 50, 5, station_ids=[MIRPUR], multiplier=2.0)
    sim.inject_fault("stale_data", 120)
    sim.run()
    time.sleep(0.5)
    before = sim.instance()
    reset = sim.post("/admin/reset")
    inst = sim.get("/v1/instance")
    after = {
        "reset_response": {"status": reset.status, "body": reset.json},
        "instance": inst.json,
        "stale_header_after_reset": inst.headers.get("x-simulator-stale", "absent"),
        "allocations": len(sim.get_json("/v1/allocations")),
        "events": len(sim.get_json("/v1/events")),
        "active_faults": [f for f in sim.get_json("/admin/faults") if f.get("active")],
        "demand_rows_mirpur": len(sim.demand_rows(MIRPUR, limit=2000)),
        "supply_statuses": dict(Counter(a["status"] for a in sim.get_json("/v1/supply-arrivals"))),
        "metrics": sim.get_json("/v1/metrics"),
        "depots": {d["id"]: d["inventory"] for d in sim.get_json("/v1/depots")},
    }
    sim.pause()
    sim.clear_faults()
    return {
        "before": before,
        "after": after,
        "verdict": "back to tick 0" if (inst.json or {}).get("tick") == 0 else "NOT back to tick 0",
        "status_after_reset_while_running": (inst.json or {}).get("status"),
    }


# -- E9: event timing + demand_spike arithmetic ------------------------------------------------


def e9_demand_spike(sim: Sim, slow: bool) -> dict[str, Any]:
    sim.reset()
    sim.step(18)
    base = {(r["fuel_type"], r["tick"]): r["demand_liters"] for r in sim.demand_rows(MIRPUR, 2000)}

    sim.reset()
    e1 = sim.inject_event("demand_spike", 4, 8, station_ids=[MIRPUR], multiplier=2.0)
    e2 = sim.inject_event("demand_spike", 8, 8, station_ids=[MIRPUR], multiplier=1.5)
    visible = [(e["id"], e["status"]) for e in sim.get_json("/v1/events")]
    timeline = []
    for _ in range(19):
        tick = sim.tick()
        timeline.append(
            {
                "tick": tick,
                "multiplier": sim.station(MIRPUR)["demand_multiplier"],
                "ev1": (sim.event(e1.json["id"]) or {}).get("status"),
                "ev2": (sim.event(e2.json["id"]) or {}).get("status"),
            }
        )
        sim.step(1)
    spiked = {
        (r["fuel_type"], r["tick"]): r["demand_liters"] for r in sim.demand_rows(MIRPUR, 2000)
    }
    ratios: dict[int, Any] = {}
    for (fuel, t), v in sorted(spiked.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        if base.get((fuel, t)):
            ratios.setdefault(t, {})[fuel] = r4(v / base[(fuel, t)])
    return {
        "events": {"ev1": e1.json, "ev2": e2.json},
        "visible_before_start": visible,
        "timeline": timeline,
        "demand_ratio_vs_no_event_run_by_row_tick": ratios,
        "final_multiplier": timeline[-1]["multiplier"] if timeline else None,
    }


# -- E10: shipment_delay / supply_shortfall scope --------------------------------------------


def e10_supply_events(sim: Sim, slow: bool) -> dict[str, Any]:
    sim.reset()
    before = {a["id"]: a for a in sim.get_json("/v1/supply-arrivals")}
    delay = sim.inject_event(
        "shipment_delay", 0, 1, depot_ids=[GAZIPUR], fuel_types=["DIESEL"], delay_ticks=4
    )
    right_after = {a["id"]: a for a in sim.get_json("/v1/supply-arrivals")}
    sim.step(1)
    after_delay = {a["id"]: a for a in sim.get_json("/v1/supply-arrivals")}
    short = sim.inject_event("supply_shortfall", sim.tick(), 1, depot_ids=[PATIYA], factor=0.5)
    sim.step(1)
    after_short = {a["id"]: a for a in sim.get_json("/v1/supply-arrivals")}

    def diff(a: dict[str, Any], b: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        for k, old in a.items():
            new = b.get(k, {})
            changed = {
                f: [old.get(f), new.get(f)]
                for f in ("planned_tick", "quantity", "status")
                if old.get(f) != new.get(f)
            }
            if changed:
                out.append({"id": k, "depot": old["depot_id"], "fuel": old["fuel_type"], **changed})
        return out

    delayed = diff(before, after_delay)
    arrival_check = None
    if delayed:
        target = delayed[0]
        new_tick = target["planned_tick"][1] if "planned_tick" in target else None
        if new_tick is not None and new_tick < 200:
            while sim.tick() < new_tick + 1:
                sim.step(1)
            arrival_check = next(
                a for a in sim.get_json("/v1/supply-arrivals") if a["id"] == target["id"]
            )
    return {
        "shipment_delay_event": delay.json,
        "changed_immediately_on_inject": diff(before, right_after),
        "changed_after_1_step": delayed,
        "supply_shortfall_event": short.json,
        "shortfall_changes": diff(after_delay, after_short),
        "delayed_arrival_final": arrival_check,
        "event_statuses_later": [
            (e["id"], e["type"], e["status"]) for e in sim.get_json("/v1/events")
        ],
    }


# -- E11: station outage --------------------------------------------------------------------


def e11_station_outage(sim: Sim, slow: bool) -> dict[str, Any]:
    sim.reset()
    a = sim.allocate("e11-a", PATIYA, COXSBAZAR, R_PAT_COX, "DIESEL", 2000)
    ev = sim.inject_event("station_outage", 1, 6, station_ids=[COXSBAZAR])
    timeline = []
    for _ in range(9):
        tick = sim.step(1)
        st = sim.station(COXSBAZAR)
        timeline.append(
            {
                "tick": tick,
                "station_status": st["status"],
                "diesel": st["inventory"]["DIESEL"],
                "allocation": (sim.allocation(a.json["id"]) or {}).get("status") if a.ok else None,
            }
        )
    rows = sorted(
        (r for r in sim.demand_rows(COXSBAZAR, 200) if r["fuel_type"] == "DIESEL"),
        key=lambda r: r["tick"],
    )
    return {
        "event": ev.json,
        "timeline": timeline,
        "diesel_rows": [
            {k: r[k] for k in ("tick", "demand_liters", "served_liters", "unmet_liters")}
            for r in rows
        ],
        "allocation_final": sim.allocation(a.json["id"]) if a.ok else None,
    }


EXPERIMENTS: dict[str, Callable[[Sim, bool], dict[str, Any]]] = {
    "e1": e1_demand,
    "e2": e2_idempotency,
    "e3": e3_overflow,
    "e4": e4_dispatch,
    "e5": e5_disruption,
    "e6": e6_faults,
    "e7": e7_sse,
    "e8": e8_reset,
    "e9": e9_demand_spike,
    "e10": e10_supply_events,
    "e11": e11_station_outage,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--only", default=",".join(EXPERIMENTS))
    parser.add_argument(
        "--slow", action="store_true", help="include keepalive + slow-consumer tests"
    )
    parser.add_argument("--out", type=Path, default=OUT_DIR / "phase0_results.json")
    args = parser.parse_args()

    chosen = [s.strip() for s in args.only.split(",") if s.strip()]
    unknown = sorted(set(chosen) - set(EXPERIMENTS))
    if unknown:
        parser.error(f"unknown experiment(s): {', '.join(unknown)}")

    sim = Sim(args.base_url)
    health = sim.wait_healthy()
    results: dict[str, Any] = {
        "_meta": {
            "base_url": args.base_url,
            "simulator_image": SIMULATOR_IMAGE,
            "started_at": now_iso(),
            "health": health,
            "slow": args.slow,
        },
        "experiments": {},
    }
    if args.out.exists() and set(chosen) != set(EXPERIMENTS):
        results["experiments"] = json.loads(args.out.read_text()).get("experiments", {})
    failed = 0
    try:
        for name in chosen:
            print(f"== {name} ({EXPERIMENTS[name].__name__})", flush=True)
            started = time.monotonic()
            entry: dict[str, Any] = {"name": EXPERIMENTS[name].__name__}
            try:
                entry["result"] = EXPERIMENTS[name](sim, args.slow)
                entry["ok"] = True
            except Exception:  # noqa: BLE001 - one broken experiment must not hide the rest
                entry["ok"] = False
                entry["error"] = traceback.format_exc()
                failed += 1
                print(entry["error"], file=sys.stderr)
            entry["duration_s"] = round(time.monotonic() - started, 2)
            results["experiments"][name] = entry
            verdicts = {k: v for k, v in (entry.get("result") or {}).items() if "verdict" in k}
            print(
                f"   {'ok' if entry['ok'] else 'FAILED'} in {entry['duration_s']}s {verdicts or ''}"
            )
            write_json(args.out, results)  # save progress after every experiment
    finally:
        sim.reset()
        sim.close()
    print(f"\nresults: {args.out}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
