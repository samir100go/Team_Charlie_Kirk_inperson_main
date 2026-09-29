"use client";

import {
  fmt,
  hrs,
  type Allocation,
  type Depot,
  type Risk,
  type Route,
  type Station,
} from "@/lib/types";

// Schematic layout (not geographic): Dhaka division left, Chattogram division right.
const POS: Record<string, [number, number]> = {
  "depot-gazipur": [150, 70],
  "station-tongi": [70, 230],
  "station-mirpur": [250, 250],
  "depot-patiya": [650, 80],
  "station-karnaphuli": [560, 245],
  "station-coxsbazar": [740, 260],
};
const RISK_FILL: Record<Risk, string> = {
  CRITICAL: "#dc2626",
  HIGH: "#ea580c",
  ELEVATED: "#d97706",
  NORMAL: "#0d9488",
  OUTAGE: "#52525b",
};
const ORDER: Risk[] = ["OUTAGE", "CRITICAL", "HIGH", "ELEVATED", "NORMAL"];

function worst(s: Station): { risk: Risk; hours: number | null } {
  const risk = ORDER.find((r) => s.fuels.some((f) => f.risk === r)) ?? "NORMAL";
  const hours = s.fuels
    .map((f) => f.hours_to_stockout)
    .filter((h): h is number => h != null)
    .reduce<number | null>((a, h) => (a == null || h < a ? h : a), null);
  return { risk, hours };
}

function pathFor(r: Route): string {
  const [x1, y1] = POS[r.source_depot_id] ?? [0, 0];
  const [x2, y2] = POS[r.destination_station_id] ?? [0, 0];
  const cross = Math.abs(x2 - x1) > 250; // cross-region backup routes bow upward
  const mx = (x1 + x2) / 2;
  const my = cross ? Math.min(y1, y2) - 70 : (y1 + y2) / 2;
  return `M${x1},${y1} Q${mx},${my} ${x2},${y2}`;
}

export function NetworkMap({
  stations,
  depots,
  routes,
  allocations,
}: {
  stations: Station[];
  depots: Depot[];
  routes: Route[];
  allocations: Allocation[];
}) {
  const moving = allocations.filter((a) => a.status === "IN_TRANSIT" || a.status === "PENDING");
  return (
    <svg
      viewBox="0 0 820 320"
      className="w-full rounded-lg border border-zinc-800 bg-[radial-gradient(ellipse_at_top,#18181b,#09090b)]"
      role="img"
      aria-label="Fuel network map"
      data-testid="network-map"
    >
      <text x="20" y="24" className="fill-zinc-500 text-[11px] tracking-widest">
        DHAKA DIVISION
      </text>
      <text x="800" y="24" textAnchor="end" className="fill-zinc-500 text-[11px] tracking-widest">
        CHATTOGRAM DIVISION
      </text>
      {routes.map((r) => {
        const down = r.status !== "AVAILABLE";
        return (
          <g key={r.id}>
            <path
              id={`route-${r.id}`}
              d={pathFor(r)}
              fill="none"
              stroke={down ? "#ef4444" : "#3f3f46"}
              strokeWidth={down ? 2.5 : 1.5}
              strokeDasharray={down ? "6 5" : undefined}
            >
              <title>
                {r.id}: {r.status}, {r.transit_ticks} ticks, max {fmt(r.max_shipment)} L
              </title>
            </path>
          </g>
        );
      })}
      {moving.map((a) => {
        const route = routes.find(
          (r) =>
            r.source_depot_id === a.source_depot_id &&
            r.destination_station_id === a.destination_station_id,
        );
        if (!route) return null;
        return (
          <circle key={a.id} r={a.status === "PENDING" ? 3.5 : 5} fill="#38bdf8">
            <title>
              #{a.id} {a.status} {fmt(a.quantity)} L {a.fuel_type}
            </title>
            <animateMotion dur={`${2 + route.transit_ticks}s`} repeatCount="indefinite">
              <mpath href={`#route-${route.id}`} />
            </animateMotion>
          </circle>
        );
      })}
      {depots.map((d) => {
        const [x, y] = POS[d.id] ?? [0, 0];
        const total = Object.values(d.inventory).reduce((a, b) => a + b, 0);
        return (
          <g key={d.id}>
            <rect
              x={x - 46}
              y={y - 20}
              width={92}
              height={40}
              rx={8}
              fill="#1e3a8a"
              stroke={d.status === "OPEN" ? "#60a5fa" : "#f59e0b"}
              strokeWidth={1.5}
            />
            <text
              x={x}
              y={y - 3}
              textAnchor="middle"
              className="fill-sky-100 text-[11px] font-semibold"
            >
              {d.name.replace(" Depot", "")}
            </text>
            <text x={x} y={y + 12} textAnchor="middle" className="fill-sky-300 text-[10px]">
              {fmt(total / 1000)}k L · {d.status}
            </text>
          </g>
        );
      })}
      {stations.map((s) => {
        const [x, y] = POS[s.id] ?? [0, 0];
        const { risk, hours } = worst(s);
        return (
          <g key={s.id}>
            {(risk === "CRITICAL" || risk === "OUTAGE") && (
              <circle cx={x} cy={y} r={22} fill={RISK_FILL[risk]} opacity={0.35}>
                <animate attributeName="r" values="20;30;20" dur="1.6s" repeatCount="indefinite" />
                <animate
                  attributeName="opacity"
                  values="0.45;0;0.45"
                  dur="1.6s"
                  repeatCount="indefinite"
                />
              </circle>
            )}
            <circle cx={x} cy={y} r={20} fill={RISK_FILL[risk]} stroke="#fafafa" strokeWidth={1} />
            <text x={x} y={y + 4} textAnchor="middle" className="fill-white text-[10px] font-bold">
              {hours == null ? "24h+" : `${Math.round(hours)}h`}
            </text>
            <text x={x} y={y + 36} textAnchor="middle" className="fill-zinc-300 text-[11px]">
              {s.name.split(" ")[0]}
              {s.demand_multiplier !== 1 ? ` ×${s.demand_multiplier}` : ""}
            </text>
            <title>
              {s.name}: {risk}, empties in {hrs(hours)}
            </title>
          </g>
        );
      })}
    </svg>
  );
}
