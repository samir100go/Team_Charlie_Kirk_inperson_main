import type { Risk } from "@/lib/types";

export const RISK_STYLE: Record<Risk, string> = {
  CRITICAL: "bg-red-600/30 text-red-200 border-red-500/60",
  HIGH: "bg-orange-500/25 text-orange-200 border-orange-400/60",
  ELEVATED: "bg-amber-400/20 text-amber-200 border-amber-400/50",
  NORMAL: "bg-teal-500/15 text-teal-200 border-teal-400/40",
  OUTAGE: "bg-zinc-600/40 text-zinc-300 border-zinc-500",
};

export function RiskBadge({ risk }: { risk: Risk }) {
  return (
    <span className={`rounded border px-1 text-[10px] font-semibold ${RISK_STYLE[risk]}`}>
      {risk}
    </span>
  );
}
