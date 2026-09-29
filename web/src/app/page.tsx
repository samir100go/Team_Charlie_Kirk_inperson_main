"use client";

import { useCallback, useEffect, useState } from "react";

import { RecommendationCard } from "@/components/RecommendationCard";
import { StationsTable } from "@/components/StationsTable";
import { fmt, pct, type Recommendation, type World } from "@/lib/types";

export default function Home() {
  const [world, setWorld] = useState<World | null>(null);
  const [apiDown, setApiDown] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/state", { cache: "no-store" });
      if (!res.ok) throw new Error(String(res.status));
      setWorld((await res.json()) as World);
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

  async function approve(rec: Recommendation, reviewed: boolean) {
    setBusy(rec.id);
    setMsg(null);
    try {
      const res = await fetch(`/api/approve/${encodeURIComponent(rec.id)}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ reviewed }),
      });
      const body = await res.json();
      if (res.ok) {
        const a = body.allocation;
        setMsg(
          `Allocation #${a.id} ${a.status}: ${fmt(a.quantity)} L ${a.fuel_type} → ${a.destination_station_id} (key ${a.idempotency_key})`,
        );
      } else {
        const d = body.detail;
        setMsg(
          `Not sent: ${typeof d === "string" ? d : (d?.message ?? d?.code ?? JSON.stringify(d))}`,
        );
      }
      await load();
    } finally {
      setBusy(null);
    }
  }

  const w = world; // only replaced on success, so it is always the last good snapshot
  const degraded = apiDown || (w !== null && !w.meta.simulator_available);
  const fallback = w?.policy === "fallback";

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
        {w?.policy && (
          <span
            data-testid="policy"
            className={`rounded px-2 py-0.5 text-xs font-semibold ${fallback ? "bg-amber-600 text-white" : "bg-sky-900/60 text-sky-200"}`}
          >
            {fallback
              ? "FALLBACK POLICY · prediction service unavailable"
              : `FORECAST POLICY · ${w.model_version}${w.forecast_mape != null ? ` · MAPE ${pct(w.forecast_mape)}` : ""}`}
          </span>
        )}
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
      {fallback && !degraded && (
        <div
          role="alert"
          className="mb-4 rounded border border-amber-500 bg-amber-950 px-4 py-3 text-amber-100"
        >
          <strong>Prediction service unavailable – fallback allocation policy active.</strong>{" "}
          <span className="text-amber-300">
            Recommendations use the recent-demand-rate rule until forecasts return.{" "}
            {w?.meta.prediction_service.error}
          </span>
        </div>
      )}

      {!w?.ready ? (
        <p className="text-zinc-400">Waiting for the first simulator snapshot…</p>
      ) : (
        <div className="grid gap-6 xl:grid-cols-[1fr_1fr]">
          <section className="flex flex-wrap gap-4 xl:col-span-2">
            <Kpi
              label="Service level"
              value={`${((w.metrics?.service_level ?? 1) * 100).toFixed(1)}%`}
            />
            <Kpi label="Unmet demand" value={`${fmt(w.metrics?.unmet_demand_liters ?? 0)} L`} />
            <Kpi label="Served demand" value={`${fmt(w.metrics?.served_demand_liters ?? 0)} L`} />
            {w.depots?.map((d) => (
              <Kpi
                key={d.id}
                label={`${d.name} · dispatch free this tick`}
                value={`${fmt(d.dispatch_left)} / ${fmt(d.dispatch_capacity_per_tick)} L`}
                note={
                  d.dispatch_planned > 0
                    ? `${fmt(d.dispatch_planned)} L in recommendations`
                    : undefined
                }
              />
            ))}
          </section>

          {(w.events?.length ?? 0) > 0 && (
            <section
              className="rounded border border-amber-500/60 bg-amber-950/40 px-4 py-2 text-sm xl:col-span-2"
              data-testid="events"
            >
              <span className="font-semibold text-amber-300">Crisis events: </span>
              {w.events?.map((e) => (
                <span key={e.id} className="mr-4">
                  {e.type} <span className="font-semibold">{e.status}</span> (ticks {e.start_tick}–
                  {e.end_tick}
                  {typeof e.parameters.multiplier === "number" && `, ×${e.parameters.multiplier}`})
                </span>
              ))}
            </section>
          )}

          <section>
            <h2 className="mb-2 text-lg font-semibold">
              Stations · time to stockout{" "}
              <span className="text-sm font-normal text-zinc-400">
                (P50 · probability of a stockout within 24 h)
              </span>
            </h2>
            <StationsTable stations={w.stations ?? []} />

            <h2 className="mb-2 mt-6 text-lg font-semibold">Recent allocations</h2>
            <ul className="space-y-1 text-sm tabular-nums" data-testid="allocations">
              {w.allocations?.length === 0 && <li className="text-zinc-500">None yet.</li>}
              {w.allocations?.map((a) => (
                <li key={a.id}>
                  #{a.id} <span className="font-semibold">{a.status}</span> · {fmt(a.quantity)} L{" "}
                  {a.fuel_type} → {a.destination_station_id} · created tick {a.created_tick}
                  {a.expected_arrival_tick != null && ` · ETA tick ${a.expected_arrival_tick}`}
                  {a.failure_reason && ` · ${a.failure_reason}`}
                </li>
              ))}
            </ul>
          </section>

          <section>
            <h2 className="mb-2 text-lg font-semibold">
              Recommendations{" "}
              <span className="text-sm font-normal text-zinc-400">
                (most urgent first · fits each depot&apos;s dispatch capacity · you approve every
                shipment)
              </span>
            </h2>
            {msg && <p className="mb-2 rounded bg-zinc-800 px-3 py-2 text-sm">{msg}</p>}
            {w.recommendations?.length === 0 && (
              <p className="text-zinc-500">
                {(w.waiting?.length ?? 0) > 0
                  ? "This tick's dispatch capacity is fully committed; the rest ship next tick."
                  : "No station is at risk within 24 h."}
              </p>
            )}
            <ul className="space-y-2">
              {w.recommendations?.map((r) => (
                <RecommendationCard
                  key={r.id}
                  rec={r}
                  disabled={busy !== null || degraded}
                  busy={busy === r.id}
                  onApprove={approve}
                />
              ))}
            </ul>
            {(w.waiting?.length ?? 0) > 0 && (
              <>
                <h3 className="mb-1 mt-4 text-sm font-semibold text-zinc-400">
                  Waiting for next tick ({w.waiting?.length})
                </h3>
                <ul className="space-y-1 text-sm" data-testid="waiting">
                  {w.waiting?.map((q) => (
                    <li
                      key={`${q.station_id}-${q.fuel}`}
                      className="flex items-center gap-3 rounded border border-zinc-800 px-3 py-1.5 text-zinc-400"
                    >
                      <span>
                        {q.station} {q.fuel} · empties in{" "}
                        {q.hours_to_stockout == null ? ">24 h" : `~${q.hours_to_stockout} h`} ·{" "}
                        {q.reason}
                      </span>
                      <span className="ml-auto rounded bg-zinc-800 px-2 py-0.5 text-xs">
                        next tick
                      </span>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </section>
        </div>
      )}
    </main>
  );
}

function Kpi({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="rounded border border-zinc-700 bg-zinc-900 px-4 py-3">
      <div className="text-xs uppercase tracking-wide text-zinc-400">{label}</div>
      <div className="text-2xl font-semibold tabular-nums">{value}</div>
      {note && <div className="text-xs text-zinc-400">{note}</div>}
    </div>
  );
}
