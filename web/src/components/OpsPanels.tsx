"use client";

import { AnimatePresence, motion } from "motion/react";

import {
  fmt,
  hrs,
  pct,
  type Alert,
  type Decision,
  type Me,
  type ResilienceRow,
  type SimEvent,
  type SupplyArrival,
  type SystemStatus,
} from "@/lib/types";
import { cn } from "@/lib/utils";

const STATE_DOT = { Healthy: "bg-emerald-400", Degraded: "bg-amber-400", Down: "bg-red-500" };
const STATE_TEXT = {
  Healthy: "text-emerald-300",
  Degraded: "text-amber-300",
  Down: "text-red-300",
};
const SEVERITY = {
  critical: "border-red-500/60 bg-red-950/50 text-red-100",
  warning: "border-amber-500/60 bg-amber-950/40 text-amber-100",
  info: "border-sky-500/50 bg-sky-950/40 text-sky-100",
};

export function Panel({
  title,
  children,
  className,
  testId,
}: {
  title: string;
  children: React.ReactNode;
  className?: string;
  testId?: string;
}) {
  return (
    <section
      className={cn(
        "rounded-xl border border-zinc-800 bg-zinc-900/60 p-4 backdrop-blur",
        className,
      )}
      data-testid={testId}
    >
      <h2 className="mb-3 text-xs font-semibold uppercase tracking-[0.2em] text-zinc-400">
        {title}
      </h2>
      {children}
    </section>
  );
}

export function SystemStatusPanel({ status }: { status: SystemStatus | null }) {
  if (!status) return <Panel title="System status">…</Panel>;
  return (
    <Panel title="System status" testId="system-status">
      <ul className="space-y-1.5 text-sm">
        {status.components.map((c) => (
          <li key={c.name} className="flex items-center gap-2" title={c.detail}>
            <span className={cn("h-2.5 w-2.5 rounded-full", STATE_DOT[c.state])} />
            <span className="w-40 text-zinc-200">{c.name}</span>
            <span className={cn("font-semibold", STATE_TEXT[c.state])}>{c.state}</span>
            <span className="ml-auto truncate text-xs text-zinc-500">{c.detail}</span>
          </li>
        ))}
      </ul>
      <div className="mt-3 flex gap-6 border-t border-zinc-800 pt-3 text-sm tabular-nums">
        <div>
          <div className="text-xs text-zinc-500">P95 latency</div>
          <div className="font-semibold">
            {status.p95_latency_ms == null ? "–" : `${status.p95_latency_ms} ms`}
          </div>
        </div>
        <div>
          <div className="text-xs text-zinc-500">Error rate</div>
          <div className="font-semibold">{(status.error_rate * 100).toFixed(1)}%</div>
        </div>
        <div className="text-xs text-zinc-500">core-api, last {status.window_s / 60} min</div>
      </div>
    </Panel>
  );
}

const TRIGGERS: Record<string, { label: string; action: string; body?: object }[]> = {
  "ML model unavailable": [
    {
      label: "Take prediction service down 60 s",
      action: "prediction-outage",
      body: { seconds: 60 },
    },
  ],
  "Invalid simulator response": [
    { label: "Corrupt simulator data 20 s", action: "corrupt-simulator", body: { seconds: 20 } },
  ],
  "Prediction confidence too low": [
    { label: "Dhaka demand spike ×3", action: "demand-spike", body: { multiplier: 3, ticks: 48 } },
  ],
  "Backend dependency unavailable": [
    {
      label: "Simulator errors 90 % for 60 s",
      action: "simulator-fault",
      body: { type: "error_rate", rate: 0.9, seconds: 60 },
    },
  ],
};

export function ResiliencePanel({
  rows,
  me,
  onTrigger,
}: {
  rows: ResilienceRow[];
  me: Me;
  onTrigger: (action: string, body?: object) => void;
}) {
  const admin = me?.role === "admin";
  return (
    <Panel title="Resilience · brief §11" testId="resilience">
      <table className="w-full text-sm">
        <tbody>
          {rows.map((r) => (
            <tr key={r.condition} className="border-b border-zinc-800/70 last:border-0">
              <td className="py-1.5 pr-2">
                <span
                  className={cn(
                    "mr-2 inline-block rounded px-1.5 text-[10px] font-bold",
                    r.active ? "animate-pulse bg-red-600 text-white" : "bg-zinc-800 text-zinc-400",
                  )}
                >
                  {r.active ? "ACTIVE" : "OK"}
                </span>
                {r.condition}
              </td>
              <td className="py-1.5 text-zinc-400">→ {r.response}</td>
              <td className="py-1.5 text-right">
                {admin &&
                  TRIGGERS[r.condition]?.map((t) => (
                    <button
                      key={t.action}
                      type="button"
                      onClick={() => onTrigger(t.action, t.body)}
                      className="rounded border border-zinc-700 px-2 py-0.5 text-xs text-zinc-300 hover:border-red-500 hover:text-red-300"
                    >
                      {t.label}
                    </button>
                  ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {admin ? (
        <button
          type="button"
          onClick={() => onTrigger("clear")}
          className="mt-2 rounded bg-zinc-800 px-3 py-1 text-xs text-zinc-200 hover:bg-zinc-700"
        >
          Clear all injected failures
        </button>
      ) : (
        <p className="mt-2 text-xs text-zinc-500">
          Log in as admin to inject failures for the demo.
        </p>
      )}
    </Panel>
  );
}

export function AlertsPanel({ open, recent }: { open: Alert[]; recent: Alert[] }) {
  const resolved = recent.filter((a) => a.resolved_at).slice(0, 6);
  return (
    <Panel title={`System alerts · ${open.length} open`} testId="alerts">
      <ul className="space-y-1.5">
        <AnimatePresence initial={false}>
          {open.map((a) => (
            <motion.li
              key={`${a.source}-${a.kind}`}
              layout
              initial={{ opacity: 0, scale: 0.9, y: -8 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.9 }}
              transition={{ type: "spring", stiffness: 350, damping: 30 }}
              className={cn("rounded border px-3 py-1.5 text-xs", SEVERITY[a.severity])}
            >
              <span className="font-bold uppercase">{a.severity}</span> · {a.source} · {a.message}
            </motion.li>
          ))}
        </AnimatePresence>
        {open.length === 0 && <li className="text-xs text-zinc-500">No open alerts.</li>}
      </ul>
      {resolved.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-[11px] text-zinc-500">
          {resolved.map((a) => (
            <li key={a.id}>
              ✓ resolved {new Date(a.resolved_at ?? a.raised_at).toLocaleTimeString()} · {a.kind}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

const OUTCOME = {
  ARRIVED: "text-emerald-300",
  IN_TRANSIT: "text-sky-300",
  PENDING: "text-zinc-300",
  FAILED: "text-red-300",
  CANCELLED: "text-zinc-500",
};

export function DecisionHistory({ decisions }: { decisions: Decision[] }) {
  return (
    <Panel title="Decision history · audit" testId="decisions">
      {decisions.length === 0 ? (
        <p className="text-xs text-zinc-500">No decisions yet.</p>
      ) : (
        <table className="w-full text-xs tabular-nums">
          <thead className="text-left text-zinc-500">
            <tr>
              <th>When</th>
              <th>Who</th>
              <th>Decision</th>
              <th>Risk before → after</th>
              <th>Outcome</th>
            </tr>
          </thead>
          <tbody>
            {decisions.slice(0, 12).map((d) => (
              <tr key={d.id} className="border-t border-zinc-800/70">
                <td className="py-1 pr-2 text-zinc-400">
                  {new Date(d.decided_at).toLocaleTimeString()}
                  <div className="text-[10px]">tick {d.sim_tick}</div>
                </td>
                <td className="pr-2">
                  {d.decided_by}
                  <div className="text-[10px] text-zinc-500">
                    {d.role}
                    {d.review_required ? " · reviewed" : ""}
                  </div>
                </td>
                <td className="pr-2">
                  {fmt(d.quantity)} L {d.fuel} → {d.station_id.replace("station-", "")}
                  <div className="text-[10px] text-zinc-500">
                    {d.route_id} · {d.policy}
                    {d.confidence != null && ` · conf ${pct(d.confidence)}`}
                  </div>
                </td>
                <td className="pr-2">
                  {d.risk_before && d.risk_after
                    ? `${hrs(d.risk_before.hours_p50)} → ${hrs(d.risk_after.hours_p50)}`
                    : "–"}
                  {d.risk_before && d.risk_after && (
                    <div className="text-[10px] text-zinc-500">
                      unmet {fmt(d.risk_before.unmet_p50)} → {fmt(d.risk_after.unmet_p50)} L
                    </div>
                  )}
                </td>
                <td
                  className={cn(
                    "font-semibold",
                    d.result !== "accepted"
                      ? "text-red-300"
                      : OUTCOME[(d.outcome ?? "PENDING") as keyof typeof OUTCOME],
                  )}
                >
                  {d.result === "accepted" ? (d.outcome ?? "PENDING") : d.result.toUpperCase()}
                  {d.allocation_id != null && (
                    <div className="text-[10px] font-normal text-zinc-500">#{d.allocation_id}</div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}

export function SupplyAndEvents({
  supply,
  events,
}: {
  supply: SupplyArrival[];
  events: SimEvent[];
}) {
  return (
    <Panel title="Incoming supply & disruptions" testId="supply-events">
      <h3 className="mb-1 text-xs text-zinc-500">Next depot supply arrivals</h3>
      <ul className="mb-3 space-y-0.5 text-xs tabular-nums">
        {supply.length === 0 && (
          <li className="text-amber-300">
            No more supply scheduled: the network is running on stock.
          </li>
        )}
        {supply.map((s) => (
          <li key={s.id} className="flex gap-2">
            <span className="w-28 text-zinc-300">{s.depot_id.replace("depot-", "")}</span>
            <span className="w-14">{s.fuel_type}</span>
            <span className="w-20 text-right">{fmt(s.quantity)} L</span>
            <span className="text-zinc-400">
              tick {s.planned_tick} (in {s.eta_hours} h)
              {s.status === "DELAYED" && <span className="ml-1 text-amber-300">DELAYED</span>}
            </span>
          </li>
        ))}
      </ul>
      <h3 className="mb-1 text-xs text-zinc-500">Disruptions & crisis events</h3>
      <ul className="space-y-0.5 text-xs">
        {events.length === 0 && <li className="text-zinc-500">None active or scheduled.</li>}
        {events.map((e) => (
          <li key={e.id}>
            <span className={e.status === "ACTIVE" ? "text-red-300" : "text-amber-300"}>
              {e.status}
            </span>{" "}
            {e.type} · ticks {e.start_tick}–{e.end_tick}
            <span className="text-zinc-500"> · {JSON.stringify(e.parameters)}</span>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
