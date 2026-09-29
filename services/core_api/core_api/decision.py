"""Decision engine: turn the world + predictions into inspectable, capacity-feasible refills.

Policy "forecast" (prediction service up): risk = stockout probability and time-to-stockout
from the intelligence service; quantity = forecast need over transit + 24 h, capped by the
binding constraint. Policy "fallback" (prediction service down, brief §11): the recent-rate
rule, clearly labelled. Either way candidates are served most-urgent-first against each
depot's per-tick dispatch capacity, depot stock and station room (capacity - inventory -
fuel in flight: the simulator destroys overflow), and what doesn't fit waits for next tick.
"""

from __future__ import annotations

import math
import statistics
from typing import Any

from jalani_common.projection import Projection, project

RATE_WINDOW_TICKS = 8
HORIZON_TICKS = 96
CRITICAL_H, HIGH_H, WATCH_H = 8.0, 16.0, 24.0
WATCH_PROB = 0.2
REVIEW_BELOW_CONFIDENCE = 0.6
MIN_SHIPMENT_L = 500.0
FUELS = ("DIESEL", "PETROL", "OCTANE")
IN_FLIGHT = {"PENDING", "IN_TRANSIT"}
NETWORK_EVENTS = {"route_disruption", "depot_constraint", "shipment_delay", "supply_shortfall"}


def demand_rate_per_tick(rows: list[dict[str, Any]], fuel: str) -> float:
    """Mean demand (L/tick) over the newest RATE_WINDOW_TICKS rows for one fuel (fallback)."""
    recent = [r["demand_liters"] for r in rows if r["fuel_type"] == fuel][:RATE_WINDOW_TICKS]
    return statistics.fmean(recent) if recent else 0.0


def risk_tier(hours: float | None, prob: float | None, status: str) -> str:
    if status != "OPEN":
        return "OUTAGE"
    if hours is not None and hours <= CRITICAL_H:
        return "CRITICAL"
    if hours is not None and hours <= HIGH_H:
        return "HIGH"
    if (hours is not None and hours <= WATCH_H) or (prob is not None and prob >= WATCH_PROB):
        return "ELEVATED"
    return "NORMAL"


def incoming_schedule(w: dict[str, Any]) -> dict[tuple[str, str], list[tuple[int, float]]]:
    """(station, fuel) -> [(arrival_tick, liters)] for PENDING and IN_TRANSIT allocations."""
    tick = w["instance"]["tick"]
    transit = {r["id"]: r["transit_ticks"] for r in w["routes"]}
    out: dict[tuple[str, str], list[tuple[int, float]]] = {}
    for a in w["allocations"]:
        if a["status"] not in IN_FLIGHT:
            continue
        eta = a.get("expected_arrival_tick")
        if eta is None:  # PENDING: departs on the next step
            eta = tick + transit.get(a.get("route_id", ""), 2)
        key = (a["destination_station_id"], a["fuel_type"])
        out.setdefault(key, []).append((int(eta), float(a["quantity"])))
    return out


def disrupted_at_departure(events: list[dict[str, Any]], tick: int) -> dict[str, int]:
    """Routes a new allocation must avoid: route_id -> event id.

    An allocation created now departs while the simulator processes `tick`, after events
    starting at `tick` have activated. A route_disruption covering that tick makes the
    allocation FAIL at departure, and a FAILED allocation is not refunded (SIMULATOR_NOTES
    #5), so a route that still reads AVAILABLE can already be unusable.
    """
    blocked: dict[str, int] = {}
    for ev in events:
        if ev.get("type") != "route_disruption" or ev.get("status") not in {"ACTIVE", "SCHEDULED"}:
            continue
        if int(ev["start_tick"]) <= tick <= int(ev["end_tick"]):
            for route_id in (ev.get("parameters") or {}).get("route_ids") or []:
                blocked[route_id] = int(ev["id"])
    return blocked


def _arrivals(sched: list[tuple[int, float]], tick: int, n: int) -> list[float]:
    arr = [0.0] * n
    for eta, qty in sched:
        k = max(0, eta - tick)
        if k < n:
            arr[k] += qty
    return arr


def _fmt_h(h: float | None, horizon: float) -> str:
    return f">{horizon:.0f} h" if h is None else f"{h:.1f} h"


def _risk_dict(p: Projection) -> dict[str, Any]:
    return {**p.as_dict(), "tier": risk_tier(p.hours_p50, p.stockout_prob, "OPEN")}


def plan(w: dict[str, Any], predictions: dict[str, Any] | None = None) -> dict[str, Any]:
    tick = w["instance"]["tick"]
    tick_h = w["instance"]["tick_minutes"] / 60
    preds: dict[tuple[str, str], dict[str, Any]] = {}
    if predictions and predictions.get("tick") == tick:
        preds = {(p["station_id"], p["fuel"]): p for p in predictions["predictions"]}
    policy = "forecast" if preds else "fallback"

    sched = incoming_schedule(w)
    dispatched: dict[str, float] = {}
    shipped_now: set[tuple[str, str]] = set()
    for a in w["allocations"]:
        if a["status"] == "PENDING":  # PENDING = created this tick = uses this tick's cap
            dispatched[a["source_depot_id"]] = (
                dispatched.get(a["source_depot_id"], 0.0) + a["quantity"]
            )
            shipped_now.add((a["destination_station_id"], a["fuel_type"]))
    depots = {d["id"]: d for d in w["depots"]}
    dispatch_left = {
        d["id"]: max(0.0, float(d["dispatch_capacity_per_tick"]) - dispatched.get(d["id"], 0.0))
        for d in w["depots"]
    }
    free_now = dict(dispatch_left)
    stock_left = {d["id"]: {f: float(d["inventory"][f]) for f in FUELS} for d in w["depots"]}
    active_events = [e for e in w.get("events", []) if e["status"] in {"ACTIVE", "SCHEDULED"}]
    blocked = disrupted_at_departure(active_events, tick)

    stations_out: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for st in w["stations"]:
        fuels = []
        for fuel in FUELS:
            inv, cap = float(st["inventory"][fuel]), float(st["capacity"][fuel])
            s_arr = sched.get((st["id"], fuel), [])
            incoming = sum(q for _, q in s_arr)
            pred = preds.get((st["id"], fuel))
            if pred is not None:
                mean, sd = pred["curve_mean"], pred["curve_sd"]
                serving = pred["serving"]
                rate_h = pred["rate_lph_p50"]
            else:
                rate_t = demand_rate_per_tick(w["demand"].get(st["id"], []), fuel)
                mean, sd = [rate_t] * HORIZON_TICKS, [rate_t * 0.1] * HORIZON_TICKS
                serving = [st["status"] == "OPEN"] * HORIZON_TICKS
                rate_h = rate_t / tick_h
            before = project(
                inventory=inv,
                capacity=cap,
                demand_mean=mean,
                demand_sd=sd,
                arrivals=_arrivals(s_arr, tick, len(mean)),
                serving=serving,
                tick_hours=tick_h,
            )
            tier = risk_tier(before.hours_p50, before.stockout_prob, st["status"])
            cell = {
                "fuel": fuel,
                "inventory": round(inv, 1),
                "capacity": cap,
                "in_flight": incoming,
                "demand_lph": round(rate_h, 1),
                "hours_to_stockout": before.hours_p50,
                "hours_range": [before.hours_early, before.hours_late],
                "stockout_prob": round(before.stockout_prob, 3) if pred else None,
                "risk": tier,
                "anomaly": pred["anomaly"] if pred else None,
                "confidence": pred["confidence"] if pred else None,
                "demand_24h": pred["demand_24h"] if pred else None,
            }
            fuels.append(cell)
            if tier in {"NORMAL", "OUTAGE"} or (st["id"], fuel) in shipped_now:
                continue
            candidates.append(
                {
                    "st": st,
                    "fuel": fuel,
                    "inv": inv,
                    "cap": cap,
                    "incoming": incoming,
                    "sched": s_arr,
                    "mean": mean,
                    "sd": sd,
                    "serving": serving,
                    "rate_h": rate_h,
                    "before": before,
                    "pred": pred,
                }
            )
        stations_out.append(
            {
                "id": st["id"],
                "name": st["name"],
                "region_id": st["region_id"],
                "status": st["status"],
                "demand_multiplier": st["demand_multiplier"],
                "fuels": fuels,
            }
        )

    def urgency(c: dict[str, Any]) -> tuple[float, float]:
        h = c["before"].hours_p50
        return (h if h is not None else 1e9, -c["before"].stockout_prob)

    recs: list[dict[str, Any]] = []
    waiting: list[dict[str, Any]] = []
    for c in sorted(candidates, key=urgency):
        st, fuel, before = c["st"], c["fuel"], c["before"]
        room = c["cap"] - c["inv"] - c["incoming"]
        routes = sorted(
            (
                r
                for r in w["routes"]
                if r["destination_station_id"] == st["id"]
                and r["status"] == "AVAILABLE"
                and r["id"] not in blocked
            ),
            key=lambda r: r["transit_ticks"],
        )
        options = []
        for route in routes:
            src = route["source_depot_id"]
            opt = _option(
                c, route, depots[src], dispatch_left[src], stock_left[src][fuel], room, tick, tick_h
            )
            if opt is not None:
                options.append(opt)
        base = {
            "station_id": st["id"],
            "station": st["name"],
            "fuel": fuel,
            "hours_to_stockout": before.hours_p50,
            "stockout_prob": round(before.stockout_prob, 3) if c["pred"] else None,
            "demand_lph": round(c["rate_h"], 1),
        }
        if not options:
            if room < MIN_SHIPMENT_L:
                why = "tank is full once incoming fuel lands"
            elif not routes:
                why = "no open route to this station"
            else:
                why = "dispatch capacity this tick is taken by more urgent stations (or no stock)"
            waiting.append({**base, "reason": why})
            continue
        best = options[0]
        depot_id = best["depot_id"]
        dispatch_left[depot_id] -= best["quantity"]
        stock_left[depot_id][fuel] -= best["quantity"]
        recs.append(
            _recommendation(c, best, options[1:], base, w, tick, tick_h, policy, active_events)
        )

    return {
        "instance": w["instance"],
        "metrics": w["metrics"],
        "policy": policy,
        "model_version": predictions.get("model_version") if preds and predictions else None,
        "forecast_mape": predictions.get("forecast_mape") if preds and predictions else None,
        "events": active_events,
        "stations": stations_out,
        "depots": [
            {
                "id": d["id"],
                "name": d["name"],
                "status": d["status"],
                "inventory": d["inventory"],
                "dispatch_capacity_per_tick": d["dispatch_capacity_per_tick"],
                "dispatch_left": round(free_now[d["id"]], 1),
                "dispatch_planned": round(free_now[d["id"]] - dispatch_left[d["id"]], 1),
            }
            for d in w["depots"]
        ],
        "recommendations": recs,
        "waiting": waiting,
        "allocations": w["allocations"][:10],
        "routes": [
            {
                k: r[k]
                for k in (
                    "id",
                    "source_depot_id",
                    "destination_station_id",
                    "status",
                    "transit_ticks",
                    "max_shipment",
                )
            }
            for r in w["routes"]
        ],
        "supply": [
            {**a, "eta_hours": round((a["planned_tick"] - tick) * tick_h, 2)}
            for a in sorted(w.get("supply", []), key=lambda a: a["planned_tick"])
            if a["status"] in {"SCHEDULED", "DELAYED"}
        ][:8],
    }


def _option(
    c: dict[str, Any],
    route: dict[str, Any],
    depot: dict[str, Any],
    dispatch_free: float,
    stock: float,
    room: float,
    tick: int,
    tick_h: float,
) -> dict[str, Any] | None:
    """Best quantity on one route and its projected impact."""
    transit = int(route["transit_ticks"])
    day = round(24 / tick_h)
    need = sum(c["mean"][: transit + day]) - c["inv"] - c["incoming"]
    need = max(MIN_SHIPMENT_L, math.ceil(need / 100) * 100)
    limits = {
        "route max_shipment": float(route["max_shipment"]),
        f"{depot['name']} dispatch free this tick": dispatch_free,
        f"{depot['name']} {c['fuel']} stock": stock,
        "station free capacity": room,
        "forecast need (transit + 24 h)": float(need),
    }
    binding = min(limits, key=lambda k: limits[k])
    qty = math.floor(limits[binding])
    if qty < MIN_SHIPMENT_L:
        return None
    arr = _arrivals(c["sched"], tick, len(c["mean"]))
    if transit < len(arr):
        arr[transit] += qty  # departs on the next step, lands transit ticks later
    after = project(
        inventory=c["inv"],
        capacity=c["cap"],
        demand_mean=c["mean"],
        demand_sd=c["sd"],
        arrivals=arr,
        serving=c["serving"],
        tick_hours=tick_h,
    )
    return {
        "route_id": route["id"],
        "depot_id": depot["id"],
        "depot": depot["name"],
        "transit_ticks": transit,
        "eta_hours": round(transit * tick_h, 2),
        "quantity": float(qty),
        "binding_constraint": binding,
        "limits": {k: round(v, 1) for k, v in limits.items()},
        "after": after,
    }


def _recommendation(
    c: dict[str, Any],
    best: dict[str, Any],
    others: list[dict[str, Any]],
    base: dict[str, Any],
    w: dict[str, Any],
    tick: int,
    tick_h: float,
    policy: str,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    st, fuel, pred = c["st"], c["fuel"], c["pred"]
    before: Projection = c["before"]
    after: Projection = best["after"]
    horizon = before.horizon_hours

    if pred:
        why = (
            f"{st['name']} {fuel}: {before.stockout_prob:.0%} chance of running out within "
            f"{horizon:.0f} h; expected in {_fmt_h(before.hours_p50, horizon)} "
            f"(range {_fmt_h(before.hours_early, horizon)}-{_fmt_h(before.hours_late, horizon)})."
        )
    else:
        why = (
            f"{st['name']} {fuel} runs out in ~{_fmt_h(before.hours_p50, horizon)} at the recent "
            f"rate of {c['rate_h']:.0f} L/h (fallback rule: prediction service unavailable)."
        )

    signals: list[dict[str, str]] = [
        {"name": "inventory", "value": f"{c['inv']:.0f} L of {c['cap']:.0f} L"},
        {"name": "incoming", "value": f"{c['incoming']:.0f} L already in flight"},
    ]
    if pred:
        d = pred["demand_24h"]
        signals.append(
            {
                "name": "forecast demand (24 h)",
                "value": f"{d['p50']:.0f} L (P10 {d['p10']:.0f} - P90 {d['p90']:.0f})",
            }
        )
        if pred["observed_lph"] is not None:
            signals.append(
                {
                    "name": "recent demand",
                    "value": f"{pred['observed_lph']:.0f} L/h observed vs "
                    f"{pred['rate_lph_p50']:.0f} L/h forecast next hour",
                }
            )
        an = pred["anomaly"]
        if an["detected"]:
            since = f" since tick {an['since_tick']}" if an["since_tick"] is not None else ""
            signals.append(
                {
                    "name": "abnormal demand",
                    "value": f"{an['direction']} x{an['ratio']:.2f} of normal{since} "
                    f"(z={an['z']:.0f})",
                }
            )
    else:
        signals.append({"name": "recent demand", "value": f"{c['rate_h']:.0f} L/h (last 2 h)"})
    if st["demand_multiplier"] != 1:
        signals.append(
            {"name": "simulator demand multiplier", "value": f"x{st['demand_multiplier']}"}
        )
    for ev in events:
        p = ev.get("parameters") or {}
        targets = (p.get("station_ids") or []) + (p.get("region_ids") or [])
        if ev["type"] in NETWORK_EVENTS or st["id"] in targets or st["region_id"] in targets:
            window = f"ticks {ev['start_tick']}-{ev['end_tick']}"
            ids = (p.get("route_ids") or []) + (p.get("depot_ids") or [])
            if ids:
                window += " on " + ", ".join(ids)
            if ev["type"] == "route_disruption":
                window += " (avoided: a shipment departing into it FAILS and loses the fuel)"
            signals.append(
                {"name": f"event #{ev['id']}", "value": f"{ev['type']} {ev['status']} {window}"}
            )

    constraints = [
        {"name": k, "value": f"{v:.0f} L", "binding": k == best["binding_constraint"]}
        for k, v in best["limits"].items()
    ]
    constraints.append(
        {
            "name": "route",
            "value": f"{best['route_id']} AVAILABLE, {best['transit_ticks']} ticks transit",
            "binding": False,
        }
    )

    confidence = pred["confidence"] if pred else None
    review_reasons = list(pred["confidence_reasons"]) if pred else []
    review_required = confidence is not None and confidence < REVIEW_BELOW_CONFIDENCE

    alternatives = []
    for o in others[:2]:
        alternatives.append(
            {
                "action": f"ship {o['quantity']:.0f} L from {o['depot']} via {o['route_id']} "
                f"(ETA {o['eta_hours']:.1f} h)",
                "after": _risk_dict(o["after"]),
                "note": f"limited by {o['binding_constraint']}",
            }
        )
    alternatives.append(
        {
            "action": "hold: no shipment this tick",
            "after": _risk_dict(before),
            "note": "risk stays as is; re-evaluated next tick",
        }
    )

    qty = best["quantity"]
    rec_id = f"t{tick}-{st['id']}-{fuel}-{best['depot_id']}-{int(qty)}"
    return {
        **base,
        "id": rec_id,
        "policy": policy,
        "depot_id": best["depot_id"],
        "depot": best["depot"],
        "route_id": best["route_id"],
        "transit_ticks": best["transit_ticks"],
        "eta_hours": best["eta_hours"],
        "quantity": qty,
        "current_l": round(c["inv"], 1),
        "incoming_l": c["incoming"],
        "hours_cover_after": after.hours_p50,
        "warning": (
            f"expected to run dry before this shipment lands (ETA {best['eta_hours']:.1f} h); "
            "it restores supply after that"
            if before.hours_p50 is not None and before.hours_p50 <= best["eta_hours"]
            else None
        ),
        "why": why,
        "reason": f"{why} Ship {qty:.0f} L from {best['depot']} via {best['route_id']} "
        f"(ETA {best['eta_hours']:.1f} h); limited by {best['binding_constraint']}.",
        "signals": signals,
        "constraints": constraints,
        "impact": {"before": _risk_dict(before), "after": _risk_dict(after)},
        "confidence": confidence,
        "review_required": review_required,
        "review_reasons": review_reasons if review_required else [],
        "alternatives": alternatives,
    }
