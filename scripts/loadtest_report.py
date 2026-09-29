#!/usr/bin/env python3
"""Summarise `make loadtest` output (k6 summaries + docker stats samples) as a markdown table.

python scripts/loadtest_report.py 5 20 50   ->  loadtest/results/report.md (+ report.json)
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

RESULTS = Path(__file__).resolve().parent.parent / "loadtest" / "results"
WATCH = ("core-api", "intelligence", "postgres", "simulator")


def _mem_mib(text: str) -> float:
    m = re.match(r"([\d.]+)\s*([KMG]i?B)", text.strip())
    if not m:
        return 0.0
    value, unit = float(m.group(1)), m.group(2)
    return value * {
        "KiB": 1 / 1024,
        "MiB": 1,
        "GiB": 1024,
        "kB": 1 / 1000,
        "MB": 1,
        "GB": 1000,
    }.get(unit, 1)


def resources(level: str) -> dict[str, dict[str, float]]:
    path = RESULTS / f"stats-vus{level}.csv"
    samples: dict[str, list[tuple[float, float]]] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            parts = line.split(",")
            if len(parts) != 3:
                continue
            name, cpu, mem = parts
            svc = next((w for w in WATCH if w in name), None)
            if svc:
                samples.setdefault(svc, []).append(
                    (float(cpu.rstrip("%") or 0), _mem_mib(mem.split("/")[0]))
                )
    return {
        svc: {
            "cpu_avg_pct": round(statistics.fmean(c for c, _ in v), 1),
            "cpu_max_pct": round(max(c for c, _ in v), 1),
            "mem_max_mib": round(max(m for _, m in v), 1),
        }
        for svc, v in samples.items()
    }


def main(levels: list[str]) -> int:
    rows, report = [], {}
    for level in levels:
        data = json.loads((RESULTS / f"decision-vus{level}.json").read_text())
        m = data["metrics"]
        d = m["http_req_duration{path:decision}"]
        dash = m["http_req_duration{path:dashboard}"]
        failed = m["http_req_failed{path:decision}"]
        reqs = m.get("http_reqs{path:decision}")
        iters = m["iterations"]
        res = resources(level)
        entry = {
            "vus": int(level),
            "decision_ms": {
                k: round(v, 1) for k, v in d.items() if k in {"avg", "med", "p(95)", "p(99)", "max"}
            },
            "dashboard_p95_ms": round(dash["p(95)"], 1),
            "error_rate": round(failed.get("rate", failed.get("value", 0.0)), 4),
            "decision_rps": round(reqs["rate"], 1) if reqs else None,
            "total_rps": round(m["http_reqs"]["rate"], 1),
            "iterations": iters["count"],
            "resources": res,
        }
        report[level] = entry
        core, intel = res.get("core-api", {}), res.get("intelligence", {})
        rows.append(
            f"| {level} | {entry['decision_ms']['avg']} | {entry['decision_ms']['med']} | "
            f"{entry['decision_ms']['p(95)']} | {entry['decision_ms']['p(99)']} | "
            f"{entry['decision_rps']} | {entry['error_rate']:.2%} | {entry['dashboard_p95_ms']} | "
            f"{core.get('cpu_avg_pct', '-')}% / {core.get('cpu_max_pct', '-')}% | "
            f"{intel.get('cpu_avg_pct', '-')}% / {intel.get('cpu_max_pct', '-')}% | "
            f"{core.get('mem_max_mib', '-')} / {intel.get('mem_max_mib', '-')} |"
        )
    table = (
        "| VUs | avg ms | p50 ms | p95 ms | p99 ms | decisions/s | errors | dashboard p95 ms "
        "| core-api CPU avg/max | intelligence CPU avg/max | peak MiB core/intel |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|\n" + "\n".join(rows) + "\n"
    )
    (RESULTS / "report.md").write_text(table)
    (RESULTS / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or ["5", "20", "50"]))
