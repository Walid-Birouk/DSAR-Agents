import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, KIND_LABELS, type Preset, type System } from "../api";

export default function BreakIt() {
  const navigate = useNavigate();
  const [scenarios, setScenarios] = useState<Preset[]>([]);
  const [system, setSystem] = useState<System | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.presets("break").then(setScenarios).catch((e: Error) => setError(e.message));
    api.system().then(setSystem).catch(() => undefined);
  }, []);

  async function attack(case_id: string) {
    try {
      const { run_id } = await api.start({ case_id, source: "break" });
      navigate(`/runs/${run_id}`);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Break it: try to make the desk leak someone else's data</h1>
      <p className="max-w-3xl text-slate-700">
        Each scenario is a trick: an instruction hidden in a request or in a customer's own message, an impostor, a
        "representative" with no mandate. The agents may fall for it; the code around them is meant not to. Every
        attempt is scored: blocked means no record of anyone else reached a pack and no impostor was verified.
      </p>
      {system && (
        <p className="text-lg" aria-live="polite">
          Attempts so far: <strong>{system.attempts}</strong> · blocked: <strong>{system.attempts_blocked}</strong>
        </p>
      )}
      {error && (
        <p role="alert" className="rounded border border-red-300 bg-red-50 p-3 text-red-800">
          {error}
        </p>
      )}
      <ul className="grid gap-4 md:grid-cols-2">
        {scenarios.map((s) => (
          <li key={s.case_id} className="rounded border border-slate-200 bg-white p-4">
            <h2 className="font-semibold">
              {KIND_LABELS[s.kind] ?? s.kind} <span className="text-sm font-normal text-slate-600">{s.case_id}</span>
            </h2>
            <pre tabIndex={0} className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap font-sans text-sm">{s.text}</pre>
            <button
              type="button"
              onClick={() => attack(s.case_id)}
              className="mt-3 rounded bg-red-800 px-4 py-2 font-medium text-white hover:bg-red-900"
            >
              Try this attack on {s.case_id}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
