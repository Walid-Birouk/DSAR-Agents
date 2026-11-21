import { useEffect, useState } from "react";
import { api, type EvalSummary } from "../api";

const ROWS: [string, string, "gate" | "pct" | "num" | "usd"][] = [
  ["impostor_accepted", "Impostor accepted", "gate"],
  ["third_party_leaks", "Third-party data in a pack", "gate"],
  ["foreign_records", "Another person's record in a pack", "gate"],
  ["released_without_approval", "Release without a valid approval", "gate"],
  ["injection_succeeded", "Successful injections", "gate"],
  ["telemetry_leaks", "Personal data in telemetry", "gate"],
  ["advisor_payload_leaks", "Personal data sent to the advisor", "gate"],
  ["identity_accuracy", "Identity decisions right", "pct"],
  ["discovery_recall", "Discovery recall", "pct"],
  ["discovery_precision", "Discovery precision", "pct"],
  ["redaction_recall", "Redaction recall", "pct"],
  ["art15_complete", "Art. 15(1) items in the letter", "pct"],
  ["pass_rate", "Cases fully correct", "pct"],
  ["cost_per_case", "Cost per case", "usd"],
  ["latency_p50_s", "Latency p50 (s)", "num"],
];

function fmt(v: number | null | undefined, kind: string) {
  if (v === null || v === undefined) return "not measured";
  if (kind === "pct") return `${(v * 100).toFixed(1)}%`;
  if (kind === "usd") return `$${v.toFixed(4)}`;
  if (kind === "gate") return v === 0 ? "0 ✓" : `${v} ✗`;
  return v.toFixed(2);
}

export default function Evals() {
  const [reports, setReports] = useState<EvalSummary[] | null>(null);
  useEffect(() => {
    api.evals().then(setReports).catch(() => setReports([]));
  }, []);
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Eval results</h1>
      <p className="max-w-3xl text-slate-700">
        Every case is graded against the fictional shop's ground truth with exact checks. Hard gates must read 0. The
        oracle runs check the desk and the graders themselves; model runs are the ones that measure the agents.
      </p>
      {reports === null ? (
        <p>Loading…</p>
      ) : reports.length === 0 ? (
        <p>No eval report yet. Run `uv run a15 eval run --suite smoke`.</p>
      ) : (
        <div tabIndex={0} className="overflow-x-auto">
          <table className="w-full border-collapse bg-white text-left text-sm">
            <caption className="sr-only">Latest eval reports, metric by metric</caption>
            <thead>
              <tr>
                <th scope="col" className="border-b p-2">
                  Metric
                </th>
                {reports.slice(0, 4).map((r) => (
                  <th scope="col" key={r.name} className="border-b p-2">
                    {r.suite} · {r.arm} · {r.agent}
                    <br />
                    <span className={r.green ? "text-green-800" : "text-red-800"}>{r.green ? "green" : "red"}</span>{" "}
                    <span className="font-normal text-slate-500">({r.cases} cases)</span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {ROWS.map(([key, label, kind]) => (
                <tr key={key} className="border-b border-slate-100">
                  <th scope="row" className="p-2 font-normal">
                    {label}
                  </th>
                  {reports.slice(0, 4).map((r) => (
                    <td key={r.name} className="p-2">
                      {fmt(r.metrics[key], kind)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
