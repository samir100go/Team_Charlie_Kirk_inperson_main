import { fmt, hrs, pct, type Station } from "@/lib/types";

import { RISK_STYLE } from "./RiskBadge";

export function StationsTable({ stations }: { stations: Station[] }) {
  return (
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
        {stations.map((s) => (
          <tr key={s.id}>
            <td className="pr-2 align-top">
              {s.name}
              <div className="text-xs text-zinc-500">{s.region_id.replace("region-", "")}</div>
              {s.status !== "OPEN" && <span className="text-xs text-red-400">{s.status}</span>}
              {s.demand_multiplier !== 1 && (
                <span
                  className="rounded bg-amber-500/20 px-1 text-xs text-amber-300"
                  title="demand_multiplier set by an active demand_spike event"
                >
                  demand ×{s.demand_multiplier}
                </span>
              )}
            </td>
            {s.fuels.map((f) => (
              <td
                key={f.fuel}
                data-risk={f.risk}
                className={`rounded border px-2 py-1 align-top ${RISK_STYLE[f.risk]}`}
                title={
                  f.demand_24h
                    ? `forecast next 24 h: ${fmt(f.demand_24h.p50)} L (P10 ${fmt(f.demand_24h.p10)} – P90 ${fmt(f.demand_24h.p90)})`
                    : "fallback rule: recent demand rate"
                }
              >
                <div className="flex items-center gap-1 font-semibold">
                  {hrs(f.hours_to_stockout)}
                  {f.stockout_prob != null && (
                    <span className="text-xs font-normal opacity-80">
                      · {pct(f.stockout_prob)} in 24 h
                    </span>
                  )}
                  {f.anomaly?.detected && (
                    <span
                      className="ml-auto rounded bg-fuchsia-600/40 px-1 text-[10px] text-fuchsia-100"
                      title={`abnormal demand: ×${f.anomaly.ratio} of the documented profile (z=${f.anomaly.z})`}
                    >
                      {f.anomaly.direction === "spike" ? "▲ spike" : "▼ drop"}
                    </span>
                  )}
                </div>
                <div className="text-xs opacity-80">
                  {fmt(f.inventory)} L{f.in_flight > 0 && ` +${fmt(f.in_flight)}`} ·{" "}
                  {fmt(f.demand_lph)} L/h
                </div>
                {f.confidence != null && f.confidence < 0.6 && (
                  <div className="text-[10px] text-red-300">low confidence {pct(f.confidence)}</div>
                )}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
