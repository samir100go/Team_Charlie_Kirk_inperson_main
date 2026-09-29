"use client";

import { useCallback, useEffect, useState } from "react";

import { CrisisFrame, Kpi, LoginBox, RegionalDemand } from "@/components/Chrome";
import { NetworkMap } from "@/components/NetworkMap";
import {
  AlertsPanel,
  DecisionHistory,
  Panel,
  ResiliencePanel,
  SupplyAndEvents,
  SystemStatusPanel,
} from "@/components/OpsPanels";
import { RecommendationCard } from "@/components/RecommendationCard";
import { StationsTable } from "@/components/StationsTable";
import {
  fmt,
  pct,
  type Alert,
  type Decision,
  type Me,
  type Recommendation,
  type ResilienceRow,
  type SystemStatus,
  type World,
} from "@/lib/types";

async function getJson<T>(url: string): Promise<T | null> {
  try {
    const res = await fetch(url, { cache: "no-store" });
    return res.ok ? ((await res.json()) as T) : null;
  } catch {
    return null;
  }
}

export default function Home() {
  const [world, setWorld] = useState<World | null>(null);
  const [apiDown, setApiDown] = useState(false);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [resilience, setResilience] = useState<ResilienceRow[]>([]);
  const [alerts, setAlerts] = useState<{ open: Alert[]; recent: Alert[] }>({
    open: [],
    recent: [],
  });
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [me, setMe] = useState<Me>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    const w = await getJson<World>("/api/state");
    if (w) setWorld(w); // only replaced on success: always the last good snapshot
    setApiDown(w === null);
  }, []);

  const loadOps = useCallback(async () => {
    const [s, r, a, d] = await Promise.all([
      getJson<SystemStatus>("/api/core/system/status"),
      getJson<{ rows: ResilienceRow[] }>("/api/core/resilience"),
      getJson<{ open: Alert[]; recent: Alert[] }>("/api/core/alerts"),
      getJson<{ decisions: Decision[] }>("/api/core/decisions"),
    ]);
    if (s) setStatus(s);
    if (r) setResilience(r.rows);
    if (a) setAlerts(a);
    if (d) setDecisions(d.decisions);
  }, []);

  useEffect(() => {
    const first = setTimeout(() => {
      void load();
      void loadOps();
      void getJson<{ username: string; role: "operator" | "admin" }>("/api/me").then(setMe);
    }, 0);
    const t1 = setInterval(load, 1000);
    const t2 = setInterval(loadOps, 2000);
    return () => {
      clearTimeout(first);
      clearInterval(t1);
      clearInterval(t2);
    };
  }, [load, loadOps]);

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
          `✓ Allocation #${a.id} ${a.status}: ${fmt(a.quantity)} L ${a.fuel_type} → ${a.destination_station_id}`,
        );
      } else {
        const d = body.detail;
        setMsg(
          `Not sent: ${typeof d === "string" ? d : (d?.message ?? d?.code ?? JSON.stringify(d))}`,
        );
      }
      await Promise.all([load(), loadOps()]);
    } finally {
      setBusy(null);
    }
  }

  async function trigger(action: string, body?: object) {
    const res = await fetch(`/api/chaos/${action}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body ?? {}),
    });
    setMsg(res.ok ? `Injected: ${action}` : `Trigger refused (${res.status})`);
    void loadOps();
  }

  const w = world;
  const degraded = apiDown || (w !== null && !w.meta.simulator_available);
  const fallback = w?.policy === "fallback";
  const critical = w?.stations?.some((s) => s.fuels.some((f) => f.risk === "CRITICAL")) ?? false;
  const alarm = degraded || fallback || critical;
  const serviceLevel = (w?.metrics?.service_level ?? 1) * 100;

  return (
    <CrisisFrame alarm={alarm}>
      <main className="min-h-full bg-zinc-950 bg-[radial-gradient(ellipse_at_top_left,rgba(14,165,233,0.08),transparent_50%)] p-5 font-sans text-zinc-100">
        <header className="mb-4 flex flex-wrap items-center gap-3">
          <span className="rounded-full border border-amber-400/60 bg-amber-400/10 px-3 py-1 text-xs font-semibold tracking-widest text-amber-300">
            SIMULATED ENVIRONMENT
          </span>
          <h1 className="text-2xl font-semibold tracking-tight">
            JALANI <span className="text-zinc-500">· Fuel Operations Center</span>
          </h1>
          {w?.instance && (
            <span className="font-mono text-sm tabular-nums text-zinc-300" data-testid="clock">
              tick {w.instance.tick} · {w.instance.sim_time.replace("T", " ")} · {w.instance.status}
            </span>
          )}
          <span
            className={`rounded px-2 py-0.5 text-xs font-semibold ${degraded ? "bg-red-600 text-white" : "bg-emerald-600/25 text-emerald-200"}`}
          >
            {degraded
              ? `CACHED${w?.meta.age_s != null ? ` ${w.meta.age_s}s old` : ""}`
              : w?.meta.sim_stale
                ? "SIM STALE"
                : "● LIVE"}
          </span>
          {w?.policy && (
            <span
              data-testid="policy"
              className={`rounded px-2 py-0.5 text-xs font-semibold ${fallback ? "bg-amber-600 text-white" : "bg-sky-900/60 text-sky-200"}`}
            >
              {fallback
                ? "FALLBACK POLICY"
                : `FORECAST · ${w.model_version}${w.forecast_mape != null ? ` · MAPE ${pct(w.forecast_mape)}` : ""}`}
            </span>
          )}
          <div className="ml-auto">
            <LoginBox me={me} onChange={setMe} />
          </div>
        </header>

        {w?.meta.invalid_payload && (
          <div
            data-banner
            role="alert"
            className="mb-3 rounded-lg border border-red-500 bg-red-950 px-4 py-2 text-red-100"
          >
            <strong>Invalid simulator data rejected – alert raised.</strong>{" "}
            <span className="text-red-300">
              {w.meta.invalid_payload.errors[0]} · decisions use the last valid snapshot
            </span>
          </div>
        )}
        {degraded && !w?.meta.invalid_payload && (
          <div
            data-banner
            role="alert"
            className="mb-3 rounded-lg border border-red-500 bg-red-950 px-4 py-2 text-red-100"
          >
            <strong>Simulator unavailable – showing cached data.</strong>{" "}
            <span className="text-red-300">
              {apiDown ? "core-api unreachable" : w?.meta.error}
              {w?.meta.age_s != null && ` · last good data ${w.meta.age_s}s ago`}
            </span>
          </div>
        )}
        {fallback && (
          <div
            data-banner
            role="alert"
            className="mb-3 rounded-lg border border-amber-500 bg-amber-950 px-4 py-2 text-amber-100"
          >
            <strong>Prediction service unavailable – fallback allocation policy active.</strong>{" "}
            <span className="text-amber-300">Recommendations use the recent-demand-rate rule.</span>
          </div>
        )}
        {w?.meta.database && !w.meta.database.available && (
          <div
            data-banner
            role="alert"
            className="mb-3 rounded-lg border border-amber-500 bg-amber-950 px-4 py-2 text-amber-100"
          >
            <strong>Database unavailable – degraded mode.</strong>{" "}
            <span className="text-amber-300">
              Decisions still work; {w.meta.database.buffered_writes} audit record(s) buffered.
            </span>
          </div>
        )}

        {!w?.ready ? (
          <p className="text-zinc-400">Waiting for the first simulator snapshot…</p>
        ) : (
          <div className="space-y-4">
            <section className="flex flex-wrap gap-3">
              <Kpi
                label="Service level"
                value={serviceLevel}
                decimals={1}
                suffix="%"
                tone={serviceLevel >= 97 ? "good" : "bad"}
              />
              <Kpi
                label="Unmet demand"
                value={w.metrics?.unmet_demand_liters ?? 0}
                suffix=" L"
                tone={(w.metrics?.unmet_demand_liters ?? 0) > 0 ? "bad" : "default"}
              />
              <Kpi label="Served demand" value={w.metrics?.served_demand_liters ?? 0} suffix=" L" />
              {w.depots?.map((d) => (
                <Kpi
                  key={d.id}
                  label={`${d.name.replace(" Depot", "")} dispatch free`}
                  value={d.dispatch_left}
                  suffix={` / ${fmt(d.dispatch_capacity_per_tick)} L`}
                  note={
                    d.dispatch_planned > 0
                      ? `${fmt(d.dispatch_planned)} L in recommendations`
                      : undefined
                  }
                />
              ))}
            </section>

            <div className="grid gap-4 2xl:grid-cols-[1.1fr_1fr]">
              <div className="space-y-4">
                <Panel title="Network · live">
                  <NetworkMap
                    stations={w.stations ?? []}
                    depots={w.depots ?? []}
                    routes={w.routes ?? []}
                    allocations={w.allocations ?? []}
                  />
                  <div className="mt-3">
                    <RegionalDemand stations={w.stations ?? []} />
                  </div>
                </Panel>
                <Panel title="Stations · time to stockout (P50 · probability within 24 h)">
                  <StationsTable stations={w.stations ?? []} />
                </Panel>
              </div>

              <Panel
                title="Recommended allocations · you approve every shipment"
                testId="recommendations"
              >
                {msg && <p className="mb-2 rounded bg-zinc-800 px-3 py-2 text-sm">{msg}</p>}
                {w.recommendations?.length === 0 && (
                  <p className="text-sm text-zinc-500">
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
                      loggedIn={me !== null}
                      disabled={busy !== null || degraded}
                      busy={busy === r.id}
                      onApprove={approve}
                    />
                  ))}
                </ul>
                {(w.waiting?.length ?? 0) > 0 && (
                  <>
                    <h3 className="mb-1 mt-4 text-xs font-semibold uppercase tracking-widest text-zinc-500">
                      Waiting for next tick ({w.waiting?.length})
                    </h3>
                    <ul className="space-y-1 text-xs" data-testid="waiting">
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
                          <span className="ml-auto rounded bg-zinc-800 px-2 py-0.5">next tick</span>
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </Panel>
            </div>

            <div className="grid gap-4 lg:grid-cols-3">
              <SystemStatusPanel status={status} />
              <ResiliencePanel rows={resilience} me={me} onTrigger={trigger} />
              <AlertsPanel open={alerts.open} recent={alerts.recent} />
            </div>
            <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
              <DecisionHistory decisions={decisions} />
              <SupplyAndEvents supply={w.supply ?? []} events={w.events ?? []} />
            </div>
            <Panel title="Recent allocations (simulator)">
              <ul
                className="grid gap-1 text-xs tabular-nums md:grid-cols-2"
                data-testid="allocations"
              >
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
            </Panel>
          </div>
        )}
      </main>
    </CrisisFrame>
  );
}
