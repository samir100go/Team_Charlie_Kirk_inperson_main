"use client";

import { useGSAP } from "@gsap/react";
import gsap from "gsap";
import { useRef, useState } from "react";

import { NumberTicker } from "@/components/ui/number-ticker";
import { fmt, type Me, type Station } from "@/lib/types";
import { cn } from "@/lib/utils";

gsap.registerPlugin(useGSAP);

/** Flashes the page frame and slides banners in the moment the system enters trouble. */
export function CrisisFrame({ alarm, children }: { alarm: boolean; children: React.ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useGSAP(
    () => {
      if (!alarm || !ref.current) return;
      const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      if (reduce) return;
      gsap.fromTo(
        ref.current,
        { boxShadow: "inset 0 0 0 0 rgba(239,68,68,0)" },
        {
          boxShadow: "inset 0 0 0 4px rgba(239,68,68,0.85)",
          duration: 0.35,
          repeat: 3,
          yoyo: true,
          ease: "power2.inOut",
        },
      );
      const banners = ref.current.querySelectorAll("[data-banner]");
      if (banners.length) {
        gsap.from(banners, { y: -24, opacity: 0, duration: 0.5, ease: "back.out(1.7)" });
      }
    },
    { dependencies: [alarm], scope: ref },
  );
  return (
    <div ref={ref} className="min-h-full">
      {children}
    </div>
  );
}

export function Kpi({
  label,
  value,
  decimals = 0,
  suffix = "",
  note,
  tone = "default",
}: {
  label: string;
  value: number;
  decimals?: number;
  suffix?: string;
  note?: string;
  tone?: "default" | "good" | "bad";
}) {
  return (
    <div className="min-w-40 flex-1 rounded-xl border border-zinc-800 bg-zinc-900/70 px-4 py-3">
      <div className="text-[10px] uppercase tracking-[0.2em] text-zinc-500">{label}</div>
      <div
        className={cn(
          "text-2xl font-semibold",
          tone === "good" && "text-emerald-300",
          tone === "bad" && "text-red-300",
        )}
      >
        <NumberTicker value={value} decimalPlaces={decimals} />
        {suffix}
      </div>
      {note && <div className="text-xs text-zinc-500">{note}</div>}
    </div>
  );
}

export function RegionalDemand({ stations }: { stations: Station[] }) {
  const regions = new Map<string, { observed: number; forecast: number }>();
  for (const s of stations) {
    const r = regions.get(s.region_id) ?? { observed: 0, forecast: 0 };
    for (const f of s.fuels) {
      r.observed += f.demand_lph;
      r.forecast += f.demand_24h?.p50 ?? f.demand_lph * 24;
    }
    regions.set(s.region_id, r);
  }
  return (
    <div className="flex gap-3" data-testid="regional-demand">
      {[...regions.entries()].map(([id, r]) => (
        <div key={id} className="flex-1 rounded-lg border border-zinc-800 px-3 py-2 text-xs">
          <div className="text-zinc-400">{id.replace("region-", "")} region demand</div>
          <div className="text-lg font-semibold tabular-nums">{fmt(r.observed)} L/h</div>
          <div className="text-zinc-500">next 24 h forecast {fmt(r.forecast)} L</div>
        </div>
      ))}
    </div>
  );
}

export function LoginBox({ me, onChange }: { me: Me; onChange: (me: Me) => void }) {
  const [open, setOpen] = useState(false);
  const [username, setUsername] = useState("operator");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function login(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const res = await fetch("/api/login", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const body = await res.json();
    if (!res.ok) {
      setError(typeof body.detail === "string" ? body.detail : "login failed");
      return;
    }
    setPassword("");
    setOpen(false);
    onChange({ username: body.username, role: body.role });
  }

  async function logout() {
    await fetch("/api/logout", { method: "POST" });
    onChange(null);
  }

  if (me) {
    return (
      <div className="flex items-center gap-2 text-xs">
        <span className="rounded-full bg-zinc-800 px-2 py-1">
          {me.username} · <span className="font-semibold text-sky-300">{me.role}</span>
        </span>
        <button type="button" onClick={logout} className="text-zinc-400 hover:text-zinc-100">
          log out
        </button>
      </div>
    );
  }
  return (
    <div className="relative text-xs">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="rounded-full border border-zinc-700 px-3 py-1 text-zinc-200 hover:border-sky-400"
      >
        Log in to approve
      </button>
      {open && (
        <form
          onSubmit={login}
          className="absolute right-0 z-20 mt-2 w-60 space-y-2 rounded-lg border border-zinc-700 bg-zinc-900 p-3 shadow-xl"
        >
          <select
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="w-full rounded bg-zinc-800 px-2 py-1"
          >
            <option value="operator">operator</option>
            <option value="admin">admin</option>
          </select>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="password (from .env)"
            className="w-full rounded bg-zinc-800 px-2 py-1"
            autoComplete="current-password"
          />
          {error && <p className="text-red-300">{error}</p>}
          <button type="submit" className="w-full rounded bg-sky-600 py-1 font-semibold text-white">
            Log in
          </button>
        </form>
      )}
    </div>
  );
}
