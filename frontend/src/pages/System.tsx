import { useEffect, useState } from "react";
import { api, type System } from "../api";

export default function SystemPage() {
  const [s, setS] = useState<System | null>(null);
  useEffect(() => {
    api.system().then(setS).catch(() => undefined);
  }, []);
  if (!s) return <p>Loading…</p>;
  const items: [string, string][] = [
    ["Runs today", String(s.runs_today)],
    ["Finished today", String(s.finished_today)],
    ["Ended as designed", s.success_rate === null ? "-" : `${(s.success_rate * 100).toFixed(0)}%`],
    ["Model spend today", `$${s.spent_today_usd.toFixed(2)} of $${s.daily_cap_usd.toFixed(2)}`],
    ["Attacks tried", String(s.attempts)],
    ["Attacks blocked", String(s.attempts_blocked)],
    ["Mode", s.mode === "live" ? "live model" : "scripted agent"],
    ["Agents", s.arm === "team" ? "seven agents" : "one agent"],
    ["Policy", `v${s.policy_version}`],
  ];
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">System</h1>
      <dl className="grid gap-3 sm:grid-cols-3">
        {items.map(([k, v]) => (
          <div key={k} className="rounded border border-slate-200 bg-white p-4">
            <dt className="text-sm text-slate-600">{k}</dt>
            <dd className="text-xl font-semibold">{v}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
