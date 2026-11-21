import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, STATE_LABELS, type RunRow } from "../api";

export default function MyRuns() {
  const [runs, setRuns] = useState<RunRow[] | null>(null);
  useEffect(() => {
    api.runs().then(setRuns).catch(() => setRuns([]));
  }, []);
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">My runs</h1>
      <p className="text-slate-700">The cases you ran in this session. They are deleted after 24 hours.</p>
      {runs === null ? (
        <p>Loading…</p>
      ) : runs.length === 0 ? (
        <p>
          None yet. <Link className="text-indigo-700 underline" to="/">Try a request</Link>.
        </p>
      ) : (
        <ul className="divide-y divide-slate-200 rounded border border-slate-200 bg-white">
          {runs.map((r) => (
            <li key={r.run_id} className="p-3">
              <Link className="text-indigo-700 underline" to={`/runs/${r.run_id}`}>
                {r.case_id}
              </Link>{" "}
              · {r.state ? (STATE_LABELS[r.state] ?? r.state) : r.status} · {r.mode} · {r.source} ·{" "}
              {r.created_at.slice(0, 19).replace("T", " ")}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
