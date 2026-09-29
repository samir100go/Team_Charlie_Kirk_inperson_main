"use client";

import { useState } from "react";

import { BorderBeam } from "@/components/ui/border-beam";
import { fmt, hrs, pct, type Recommendation, type RiskSnapshot } from "@/lib/types";

import { RiskBadge } from "./RiskBadge";

function RiskLine({ r }: { r: RiskSnapshot }) {
  return (
    <span className="tabular-nums">
      <RiskBadge risk={r.tier} /> stockout {pct(r.stockout_prob)} in {r.horizon_hours} h · empty in{" "}
      {hrs(r.hours_p50, r.horizon_hours)} · unmet {fmt(r.unmet_p50)} L
    </span>
  );
}

export function RecommendationCard({
  rec,
  disabled,
  busy,
  loggedIn,
  onApprove,
}: {
  rec: Recommendation;
  disabled: boolean;
  busy: boolean;
  loggedIn: boolean;
  onApprove: (rec: Recommendation, reviewed: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const [reviewed, setReviewed] = useState(false);
  const { before, after } = rec.impact;

  return (
    <li
      className="relative overflow-hidden rounded-xl border border-zinc-700 bg-zinc-900 p-3 text-sm"
      data-testid="recommendation"
      data-tier={before.tier}
    >
      {before.tier === "CRITICAL" && <BorderBeam duration={4} size={90} />}
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">
          {rec.station} · {rec.fuel}
        </span>
        <span className="text-zinc-300">
          ship {fmt(rec.quantity)} L from {rec.depot} via {rec.route_id} (ETA {rec.eta_hours} h)
        </span>
        <span
          className={`ml-auto rounded px-2 py-0.5 text-xs ${rec.policy === "forecast" ? "bg-sky-900/60 text-sky-200" : "bg-amber-900/60 text-amber-200"}`}
        >
          {rec.policy === "forecast" ? "forecast policy" : "FALLBACK rule"}
        </span>
        {rec.confidence != null && (
          <span
            className={`rounded px-2 py-0.5 text-xs ${rec.confidence < 0.6 ? "bg-red-900/60 text-red-200" : "bg-zinc-800 text-zinc-300"}`}
          >
            confidence {pct(rec.confidence)}
          </span>
        )}
      </div>

      <p className="mt-1">{rec.why}</p>
      {rec.warning && <p className="mt-1 text-amber-300">⚠ {rec.warning}</p>}

      <div className="mt-2 grid gap-1 text-xs text-zinc-300">
        <div>
          <span className="text-zinc-500">before </span>
          <RiskLine r={before} />
        </div>
        <div>
          <span className="text-zinc-500">after&nbsp;&nbsp; </span>
          <RiskLine r={after} />
        </div>
      </div>

      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="mt-2 text-xs text-sky-300 hover:underline"
      >
        {open ? "hide details" : "signals, constraints, alternatives"}
      </button>
      {open && (
        <div className="mt-2 grid gap-3 text-xs md:grid-cols-3" data-testid="rec-details">
          <div>
            <h4 className="mb-1 font-semibold text-zinc-400">Signals</h4>
            <ul className="space-y-0.5">
              {rec.signals.map((s) => (
                <li key={s.name}>
                  <span className="text-zinc-500">{s.name}:</span> {s.value}
                </li>
              ))}
            </ul>
          </div>
          <div>
            <h4 className="mb-1 font-semibold text-zinc-400">Constraints</h4>
            <ul className="space-y-0.5">
              {rec.constraints.map((c) => (
                <li key={c.name} className={c.binding ? "text-amber-300" : ""}>
                  <span className="text-zinc-500">{c.name}:</span> {c.value}
                  {c.binding && " ← sets the quantity"}
                </li>
              ))}
            </ul>
          </div>
          <div>
            <h4 className="mb-1 font-semibold text-zinc-400">Alternatives</h4>
            <ul className="space-y-1">
              {rec.alternatives.map((a) => (
                <li key={a.action}>
                  {a.action}
                  <div className="text-zinc-400">
                    → <RiskLine r={a.after} /> ({a.note})
                  </div>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-3">
        {rec.review_required && (
          <label className="flex items-center gap-2 rounded border border-red-500/60 bg-red-950/40 px-2 py-1 text-xs text-red-200">
            <input
              type="checkbox"
              checked={reviewed}
              onChange={(e) => setReviewed(e.target.checked)}
            />
            Human review required: {rec.review_reasons.join("; ")}. I reviewed the signals.
          </label>
        )}
        <button
          type="button"
          onClick={() => onApprove(rec, reviewed)}
          disabled={!loggedIn || disabled || busy || (rec.review_required && !reviewed)}
          title={loggedIn ? undefined : "log in as operator to approve"}
          className="ml-auto rounded bg-teal-600 px-3 py-1 font-semibold text-white hover:bg-teal-500 disabled:opacity-40"
        >
          {!loggedIn
            ? "Log in to approve"
            : busy
              ? "Sending…"
              : rec.review_required
                ? "Approve after review"
                : "Approve"}
        </button>
      </div>
    </li>
  );
}
