"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type Fuel = {
  fuel: string;
  inventory: number;
  capacity: number;
  in_flight: number;
  demand_lph: number;
  hours_to_stockout: number | null;
  risk: "CRITICAL" | "HIGH" | "ELEVATED" | "NORMAL" | "OUTAGE";
};
type Station = { id: string; name: string; status: string; fuels: Fuel[] };
type Rec = {
  id: string;
  station: string;
  fuel: string;
  depot_id: string;
  route_id: string;
  quantity: number;
  hours_to_stockout: number | null;
  hours_cover_after: number;
  reason: string;
};
type Allocation = {
  id: number;
  destination_station_id: string;
  fuel_type: string;
  quantity: number;
  status: string;
  created_tick: number;
  expected_arrival_tick: number | null;
};
type World = {
  meta: {
    age_s: number | null;
    simulator_available: boolean;
    error: string | null;
    sim_stale: boolean;
  };
  ready: boolean;
  instance?: { tick: number; sim_time: string; status: string };
  metrics?: { service_level: number; unmet_demand_liters: number; served_demand_liters: number };
  stations?: Station[];
  recommendations?: Rec[];
  allocations?: Allocation[];
};

const RISK_STYLE: Record<Fuel["risk"], string> = {
  CRITICAL: "bg-red-600/30 text-red-200 border-red-500/60",
  HIGH: "bg-orange-500/25 text-orange-200 border-orange-400/60",
  ELEVATED: "bg-amber-400/20 text-amber-200 border-amber-400/50",
  NORMAL: "bg-teal-500/15 text-teal-200 border-teal-400/40",
  OUTAGE: "bg-zinc-600/40 text-zinc-300 border-zinc-500",
};

const fmt = (n: number) => n.toLocaleString("en-US", { maximumFractionDigits: 0 });

export default function Home() {
  const [world, setWorld] = useState<World | null>(null);
  const [apiDown, setApiDown] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const last = useRef<World | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/state", { cache: "no-store" });
      if (!res.ok) throw new Error(String(res.status));
      const data = (await res.json()) as World;
      last.current = data;
      setWorld(data);
      setApiDown(false);
    } catch {
      setApiDown(true); // keep showing the last good data we had
    }
  }, []);

  useEffect(() => {
    const first = setTimeout(load, 0);
    const timer = setInterval(load, 1000);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [load]);

  async function approve(rec: Rec) {
    setBusy(rec.id);
    setMsg(null);
    try {
      const res = await fetch(`/api/approve/${encodeURIComponent(rec.id)}`, { method: "POST" });
      const body = await res.json();
      if (res.ok) {
        const a = body.allocation;
        setMsg(
          `Allocation #${a.id} ${a.status}: ${fmt(a.quantity)} L ${a.fuel_type} → ${a.destination_station_id} (key ${a.idempotency_key})`,
        );
      } else {
        const d = body.detail;
        setMsg(`Not sent: ${typeof d === "string" ? d : (d?.code ?? JSON.stringify(d))}`);
      }
      await load();
    } finally {
      setBusy(null);
    }
  }

  const w = world ?? last.current;
  const degraded = apiDown || (w !== null && !w.meta.simulator_available);

  return (
    <main className="min-h-full bg-zinc-950 p-6 font-sans text-zinc-100">
      <header className="mb-4 flex flex-wrap items-center gap-3">
        <span className="rounded-full border border-amber-400/60 bg-amber-400/10 px-3 py-1 text-xs font-semibold tracking-widest text-amber-300">
          SIMULATED ENVIRONMENT
        </span>
        <h1 className="text-2xl font-semibold tracking-tight">JALANI · Fuel Operations</h1>
        {w?.instance && (
          <span className="font-mono text-sm tabular-nums text-zinc-300" data-testid="clock">
            tick {w.instance.tick} · {w.instance.sim_time.replace("T", " ")} · {w.instance.status}
          </span>
        )}
        <span
          className={`rounded px-2 py-0.5 text-xs font-semibold ${degraded ? "bg-red-600 text-white" : "bg-teal-600/30 text-teal-200"}`}
        >
          {degraded
            ? `CACHED${w?.meta.age_s != null ? ` ${w.meta.age_s}s old` : ""}`
            : w?.meta.sim_stale
              ? "SIM STALE"
              : "LIVE"}
        </span>
      </header>

      {degraded && (
        <div
          role="alert"
          className="mb-4 rounded border border-red-500 bg-red-950 px-4 py-3 text-red-100"
        >
          <strong>Simulator unavailable – showing cached data.</strong>{" "}
          <span className="text-red-300">
            {apiDown ? "core-api unreachable" : w?.meta.error}
            {w?.meta.age_s != null && ` · last good data ${w.meta.age_s}s ago`}
          </span>
        </div>
      )}

      {!w?.ready ? (
        <p className="text-zinc-400">Waiting for the first simulator snapshot…</p>
      ) : (
        <div className="grid gap-6 lg:grid-cols-[1fr_1fr]">
          <section className="flex gap-4 lg:col-span-2">
            <Kpi
              label="Service level"
              value={`${((w.metrics?.service_level ?? 1) * 100).toFixed(1)}%`}
            />
            <Kpi label="Unmet demand" value={`${fmt(w.metrics?.unmet_demand_liters ?? 0)} L`} />
            <Kpi label="Served demand" value={`${fmt(w.metrics?.served_demand_liters ?? 0)} L`} />
          </section>

          <section>
            <h2 className="mb-2 text-lg font-semibold">Stations · hours to stockout</h2>
            <table className="w-full border-separate border-spacing-1 text-sm tabular-nums">
              <thead>
                <tr className="text-left text-zinc-400">
                  <th>Station</th>
                  <th>DIESEL</th>
                  <th>PETROL</th>
                  <th>OCTANE</th>
                </tr>
              </thead>
              <tbody>
                {w.stations?.map((s) => (
                  <tr key={s.id}>
                    <td className="pr-2">
                      {s.name}
                      {s.status !== "OPEN" && <span className="ml-1 text-red-400">{s.status}</span>}
                    </td>
                    {s.fuels.map((f) => (
                      <td
                        key={f.fuel}
                        data-risk={f.risk}
                        className={`rounded border px-2 py-1 ${RISK_STYLE[f.risk]}`}
                        title={`${fmt(f.inventory)} / ${fmt(f.capacity)} L · ${fmt(f.demand_lph)} L/h · in flight ${fmt(f.in_flight)} L`}
                      >
                        <div className="font-semibold">
                          {f.hours_to_stockout == null ? "–" : `${f.hours_to_stockout} h`}
                        </div>
                        <div className="text-xs opacity-80">
                          {fmt(f.inventory)} L{f.in_flight > 0 && ` +${fmt(f.in_flight)}`}
                        </div>
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>

            <h2 className="mb-2 mt-6 text-lg font-semibold">Recent allocations</h2>
            <ul className="space-y-1 text-sm tabular-nums" data-testid="allocations">
              {w.allocations?.length === 0 && <li className="text-zinc-500">None yet.</li>}
              {w.allocations?.map((a) => (
                <li key={a.id}>
                  #{a.id} <span className="font-semibold">{a.status}</span> · {fmt(a.quantity)} L{" "}
                  {a.fuel_type} → {a.destination_station_id} · created tick {a.created_tick}
                  {a.expected_arrival_tick != null && ` · ETA tick ${a.expected_arrival_tick}`}
                </li>
              ))}
            </ul>
          </section>

          <section>
            <h2 className="mb-2 text-lg font-semibold">
              Recommendations{" "}
              <span className="text-sm font-normal text-zinc-400">
                (refill when under 24 h of cover · you approve every shipment)
              </span>
            </h2>
            {msg && <p className="mb-2 rounded bg-zinc-800 px-3 py-2 text-sm">{msg}</p>}
            {w.recommendations?.length === 0 && (
              <p className="text-zinc-500">No station is under 24 h of cover.</p>
            )}
            <ul className="space-y-2">
              {w.recommendations?.map((r) => (
                <li key={r.id} className="rounded border border-zinc-700 bg-zinc-900 p-3">
                  <p className="text-sm">{r.reason}</p>
                  <div className="mt-2 flex items-center gap-3 text-xs text-zinc-400">
                    <span>
                      {fmt(r.quantity)} L · {r.route_id}
                    </span>
                    <button
                      type="button"
                      onClick={() => approve(r)}
                      disabled={busy !== null || degraded}
                      className="ml-auto rounded bg-teal-600 px-3 py-1 font-semibold text-white hover:bg-teal-500 disabled:opacity-40"
                    >
                      {busy === r.id ? "Sending…" : "Approve"}
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          </section>
        </div>
      )}
    </main>
  );
}

function Kpi({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-zinc-700 bg-zinc-900 px-4 py-3">
      <div className="text-xs uppercase tracking-wide text-zinc-400">{label}</div>
      <div className="text-2xl font-semibold tabular-nums">{value}</div>
    </div>
  );
}
