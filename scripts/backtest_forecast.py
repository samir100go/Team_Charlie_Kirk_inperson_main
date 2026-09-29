#!/usr/bin/env python3
"""Backtest the demand forecaster against simple baselines on real simulator data.

--record drives the live simulator deterministically (RESETS it): 120 ticks with a x3
demand_spike on region-dhaka from tick 40 to 80, and saves every demand row to
docs/experiments/raw/backtest_rows.json. Without --record the saved rows are reused.

Scores 1-tick-ahead and 8-tick-ahead (2 h) forecasts of every station x fuel from tick 16:
  model      intelligence.forecast (structural prior + online level + regime detection +
             the known demand_spike event, exactly as production uses /v1/events)
  model_no_events  the same without event information (detection alone)
  prior      documented profile only (level fixed at 1.0)
  naive      last observed value
  ma8        mean of the last 8 observations
and the spike detection delay. Writes docs/experiments/backtest_results.json.

    uv run python scripts/backtest_forecast.py [--record]
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

from intelligence.forecast.model import (
    PROFILE_NOISE,
    base_per_tick,
    estimate_level,
    event_level_factors,
    parse_sim_time,
)

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "docs/experiments/raw/backtest_rows.json"
OUT = ROOT / "docs/experiments/backtest_results.json"
TICKS, SPIKE = 120, {"start": 40, "duration": 40, "multiplier": 3.0, "region": "region-dhaka"}
HORIZONS = (1, 8)


def record(base_url: str) -> dict[str, Any]:
    sim = httpx.Client(base_url=base_url, timeout=30)
    sim.post("/admin/faults/clear")
    sim.post("/admin/reset")
    sim.post("/admin/pause")
    sim.post(
        "/admin/events",
        json={
            "type": "demand_spike",
            "start_tick": SPIKE["start"],
            "duration_ticks": SPIKE["duration"],
            "parameters": {"region_ids": [SPIKE["region"]], "multiplier": SPIKE["multiplier"]},
        },
    )
    for _ in range(TICKS):
        sim.post("/admin/step")
    stations = sim.get("/v1/stations").json()
    regions = sim.get("/v1/regions").json()
    rows = [
        row
        for s in stations
        for row in sim.get(
            "/v1/demand-history", params={"station_id": s["id"], "limit": 2000}
        ).json()
    ]
    sim.post("/admin/reset")
    sim.post("/admin/pause")
    data = {"spike": SPIKE, "stations": stations, "regions": regions, "rows": rows}
    RAW.write_text(json.dumps(data) + "\n")
    return data


def evaluate(data: dict[str, Any]) -> dict[str, Any]:
    region = {r["id"]: r["demand_factor"] for r in data["regions"]}
    series: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in data["rows"]:
        series[(r["station_id"], r["fuel_type"])].append(r)
    stations = {s["id"]: s for s in data["stations"]}
    errors: dict[tuple[str, int, str], list[float]] = defaultdict(list)  # (method, h, period)
    detection: dict[str, int | None] = {}
    spike_start = data["spike"]["start"]
    spike_end = spike_start + data["spike"]["duration"]  # inclusive (duration + 1 ticks)

    for (sid, fuel), rows in series.items():
        rows.sort(key=lambda r: r["tick"])
        st = stations[sid]
        prof, rf = st["demand_profile"], region[st["region_id"]]
        noise = PROFILE_NOISE[prof]

        def base(r: dict[str, Any], prof: str = prof, rf: float = rf, fuel: str = fuel) -> float:
            return base_per_tick(prof, fuel, rf, parse_sim_time(r["sim_time"]).hour)

        ratios = [(r["tick"], r["demand_liters"] / base(r)) for r in rows]
        spiked = st["region_id"] == data["spike"]["region"]
        key = f"{sid}/{fuel}"
        for i in range(16, len(rows)):
            last = rows[i - 1]["tick"]  # newest processed tick; the world is at tick last + 1
            est = estimate_level(ratios[:i], noise)
            if spiked and key not in detection and est.anomaly.detected and last >= spike_start:
                detection[key] = last - spike_start
            status = (
                "SCHEDULED" if last < spike_start else "ACTIVE" if last <= spike_end else "RESOLVED"
            )
            event = {
                "type": "demand_spike",
                "status": status,
                "start_tick": spike_start,
                "end_tick": spike_end,
                "parameters": {
                    "region_ids": [data["spike"]["region"]],
                    "multiplier": data["spike"]["multiplier"],
                },
            }
            for h in HORIZONS:
                j = i + h - 1
                if j >= len(rows):
                    continue
                actual = rows[j]["demand_liters"]
                t = rows[j]["tick"]
                if not spiked:
                    period = "other region"
                elif t < spike_start:
                    period = "before spike"
                elif t <= spike_end:
                    period = "during spike"
                else:
                    period = "after spike"
                factor = event_level_factors([event], sid, st["region_id"], [t])[0]
                obs = [r["demand_liters"] for r in rows[:i]]
                preds = {
                    "model": est.level * factor * base(rows[j]),
                    "model_no_events": est.level * base(rows[j]),
                    "prior": base(rows[j]),
                    "naive": obs[-1],
                    "ma8": statistics.fmean(obs[-8:]),
                }
                for m, p in preds.items():
                    errors[(m, h, period)].append(abs(p - actual) / actual)

    table: dict[str, dict[str, float]] = {}
    for (m, h, period), errs in sorted(errors.items()):
        table.setdefault(f"{period} h={h}", {})[m] = round(statistics.fmean(errs), 4)
    delays = [d for d in detection.values() if d is not None]
    return {
        "ticks": TICKS,
        "spike": data["spike"],
        "mape": table,
        "detection_delay_ticks": {
            "series_detected": len(delays),
            "max": max(delays, default=None),
            "mean": round(statistics.fmean(delays), 2) if delays else None,
        },
        "note": "MAPE = mean |forecast - actual| / actual; noise floor ~ noise/2 (4-6 %)",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--record", action="store_true", help="re-record from the simulator")
    parser.add_argument("--base-url", default="http://localhost:8000")
    args = parser.parse_args()
    data = record(args.base_url) if args.record or not RAW.exists() else json.loads(RAW.read_text())
    result = evaluate(data)
    OUT.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
