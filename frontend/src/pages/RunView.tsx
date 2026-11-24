import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, STATE_LABELS, type RunDetail } from "../api";

type Step = { name: string; agent?: string; tool?: string; status?: string; to_state?: string; from_state?: string };

function describe(s: Step): string {
  if (s.name === "case.transition")
    return s.status === "ok"
      ? `Case moved to ${STATE_LABELS[s.to_state ?? ""] ?? s.to_state}`
      : `Refused: ${s.from_state} → ${s.to_state}`;
  if (s.name === "tool.complete") return `${s.agent}: ${s.tool} (${s.status})`;
  if (s.name === "agent.start") return `${s.agent} agent started`;
  return s.name;
}

export default function RunView() {
  const { id = "" } = useParams();
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [steps, setSteps] = useState<Step[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false);
  const live = useRef<EventSource | null>(null);

  const load = useCallback(() => api.run(id).then(setDetail).catch((e: Error) => setError(e.message)), [id]);

  useEffect(() => {
    load();
    const es = new EventSource(`/api/runs/${id}/events`);
    live.current = es;
    es.addEventListener("step", (e) => {
      const s = JSON.parse((e as MessageEvent).data) as Step;
      if (["case.transition", "tool.complete", "agent.start"].includes(s.name)) setSteps((prev) => [...prev, s]);
    });
    let poll: number | undefined;
    es.addEventListener("done", () => {
      es.close();
      load();
    });
    // A proxy in front (a CDN rewrite) may not pass the live stream through: then poll until the run is over.
    es.onerror = () => {
      es.close();
      if (poll !== undefined) return;
      poll = window.setInterval(() => {
        api
          .run(id)
          .then((d) => {
            setDetail(d);
            if (d.run.status === "done" || d.run.status === "failed") window.clearInterval(poll);
          })
          .catch(() => window.clearInterval(poll));
      }, 2000);
    };
    return () => {
      es.close();
      if (poll !== undefined) window.clearInterval(poll);
    };
  }, [id, load]);

  async function review(action: string) {
    setBusy(true);
    setError(null);
    try {
      await api.review(id, { action, note, special_category_acknowledged: ack });
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const run = detail?.run;
  const c = detail?.case;
  const finished = run && (run.status === "done" || run.status === "failed");

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-2xl font-bold">
          Case {run?.case_id ?? "…"}{" "}
          <span className="text-base font-normal text-slate-600">
            {c ? (STATE_LABELS[c.state] ?? c.state) : run?.status}
          </span>
        </h1>
        {finished && (
          <Link to={`/runs/${id}/trace`} className="text-indigo-700 underline">
            See the full trace
          </Link>
        )}
      </div>
      {error && (
        <p role="alert" className="rounded border border-red-300 bg-red-50 p-3 text-red-800">
          {error}
        </p>
      )}

      <section aria-labelledby="steps" className="rounded border border-slate-200 bg-white p-4">
        <h2 id="steps" className="font-semibold">
          {finished ? "What the agents did" : "The agents are working…"}
        </h2>
        <ol tabIndex={0} aria-live="polite" className="mt-2 max-h-64 overflow-auto text-sm">
          {steps.map((s, i) => (
            <li key={i} className={s.status && s.status !== "ok" ? "text-amber-800" : ""}>
              {describe(s)}
            </li>
          ))}
        </ol>
      </section>

      {detail?.request && (
        <section aria-labelledby="request" className="rounded border border-slate-200 bg-white p-4">
          <h2 id="request" className="font-semibold">
            The request
          </h2>
          <p className="text-sm text-slate-600">
            From {detail.request.sender}, received {detail.request.received_on}
            {c && ` · reply due ${c.deadlines.due}`}
          </p>
          <pre tabIndex={0} className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap font-sans text-sm">
            {detail.request.subject_line + "\n\n" + detail.request.text}
          </pre>
        </section>
      )}

      {detail && detail.tools.length > 0 && (
        <section aria-labelledby="tools" className="rounded border border-slate-200 bg-white p-4">
          <h2 id="tools" className="font-semibold">
            Every tool call ({detail.tools.length})
          </h2>
          <ul className="mt-2 space-y-1 text-sm">
            {detail.tools.map((t, i) => (
              <li key={i}>
                <details>
                  <summary className={t.ok ? "" : "text-amber-800"}>
                    <span className="font-mono">{t.agent}</span> → <span className="font-mono">{t.tool}</span>{" "}
                    {t.ok ? "ok" : t.error_kind}
                  </summary>
                  <pre tabIndex={0} className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-2 text-xs">{t.text}</pre>
                </details>
              </li>
            ))}
          </ul>
        </section>
      )}

      {c?.advice && c.advice.length > 0 && (
        <section aria-labelledby="advice" className="rounded border border-slate-200 bg-white p-4">
          <h2 id="advice" className="font-semibold">
            The DPO advisor's advice
          </h2>
          {c.advice.map((a, i) => (
            <p key={i} className="text-sm">
              <strong>{a.recommendation.replace("_", " ")}</strong>: {a.reasons.join(" ")} ({a.sources.join("; ")})
            </p>
          ))}
        </section>
      )}

      {detail?.pack && (
        <section aria-labelledby="pack" className="grid gap-4 md:grid-cols-2">
          <div className="rounded border border-slate-200 bg-white p-4">
            <h2 id="pack" className="font-semibold">
              The reply
            </h2>
            {c?.letter && (
              <>
                <p className="mt-1 font-medium">{c.letter.subject_line}</p>
                <pre tabIndex={0} className="mt-1 max-h-96 overflow-auto whitespace-pre-wrap font-sans text-sm">{c.letter.body}</pre>
              </>
            )}
            <p className="mt-2 text-sm">
              <a className="text-indigo-700 underline" href={`/api/runs/${id}/files/pack.pdf`}>
                Download the copy (PDF)
              </a>{" "}
              ·{" "}
              <a className="text-indigo-700 underline" href={`/api/runs/${id}/files/pack.json`}>
                the pack (JSON)
              </a>{" "}
              ·{" "}
              <a className="text-indigo-700 underline" href={`/api/runs/${id}/files/letter.md`}>
                the letter
              </a>
            </p>
          </div>
          <div className="rounded border border-slate-200 bg-white p-4 text-sm">
            <h2 className="font-semibold">The copy of the data</h2>
            <p>
              {detail.pack.records.length} records:{" "}
              {Object.entries(detail.pack.record_counts)
                .map(([s, n]) => `${s} ${n}`)
                .join(", ")}
            </p>
            <h3 className="mt-3 font-semibold">Hidden, and why</h3>
            {detail.pack.redactions.length === 0 ? (
              <p>Nothing about other people was found.</p>
            ) : (
              <ul className="list-disc pl-5">
                {detail.pack.redactions.map((r, i) => (
                  <li key={i}>
                    {r.system} {r.record_id}, {r.field}: {r.category.replace("_", " ")}
                  </li>
                ))}
              </ul>
            )}
            {detail.special_category && (
              <p className="mt-3 rounded bg-amber-50 p-2 text-amber-900">
                Special-category data (for example health) was found about the customer. It stays in the copy, and you
                must acknowledge it before you approve.
              </p>
            )}
          </div>
        </section>
      )}

      {c?.state === "NEEDS_HUMAN" && c.history.length > 0 && (
        <section aria-labelledby="with-a-person" className="rounded border border-amber-300 bg-amber-50 p-4 text-sm">
          <h2 id="with-a-person" className="font-semibold">
            With a person
          </h2>
          <p className="mt-1">{c.history[c.history.length - 1].note}</p>
          {c.advice.length > 0 && (
            <ul className="mt-2 list-disc pl-5">
              {c.advice.map((a, i) => (
                <li key={i}>
                  DPO advisor ({a.recommendation.replace("_", " ")}): {a.reasons.join("; ")}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {(c?.state === "REJECTED" || c?.state === "CLOSED") && c.refusal && (
        <section aria-labelledby="refusal" className="rounded border border-slate-200 bg-white p-4">
          <h2 id="refusal" className="font-semibold">
            The refusal letter (GDPR Art. 12(4))
          </h2>
          <p className="text-sm text-slate-700">
            Written by code, not by a model: it gives the reason and how to go ahead, never confirms that an
            account exists, and names the right to complain and to go to court.
            {c.rejection_reason && <> Reason recorded on the case: {c.rejection_reason}</>}
          </p>
          <p className="mt-2 font-medium">{c.refusal.subject_line}</p>
          <pre tabIndex={0} className="mt-1 max-h-96 overflow-auto whitespace-pre-wrap font-sans text-sm">
            {c.refusal.body}
          </pre>
          {detail?.can_send_refusal ? (
            <button
              type="button"
              disabled={busy}
              onClick={() => review("send_refusal")}
              className="mt-3 rounded bg-indigo-700 px-4 py-2 font-medium text-white hover:bg-indigo-800 disabled:bg-slate-400"
            >
              Send the refusal letter
            </button>
          ) : (
            c.state === "CLOSED" && <p className="mt-2 text-sm text-green-800">Sent to the requester.</p>
          )}
        </section>
      )}

      {detail?.can_review && (
        <section aria-labelledby="review" className="rounded border-2 border-indigo-700 bg-white p-4">
          <h2 id="review" className="text-lg font-semibold">
            You are the reviewer
          </h2>
          <p className="text-sm text-slate-700">
            Nothing is sent until you approve. Your approval is signed over this exact pack.
          </p>
          {detail.special_category && (
            <label className="mt-2 flex items-center gap-2 text-sm">
              <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />I have checked the
              special-category data
            </label>
          )}
          <label htmlFor="note" className="mt-3 block text-sm font-medium">
            Note or reason (needed for changes, rejection and extension; a rejection's reason goes to the requester)
          </label>
          <textarea
            id="note"
            rows={2}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            className="mt-1 w-full rounded border border-slate-300 p-2"
          />
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={() => review("approve")}
              className="rounded bg-green-700 px-4 py-2 font-medium text-white hover:bg-green-800 disabled:bg-slate-400"
            >
              Approve and send
            </button>
            {(
              [
                ["request_changes", "Request changes"],
                ["reject", "Reject"],
                ["extend", "Extend the deadline"],
                ["escalate", "Escalate"],
              ] as const
            ).map(([action, label]) => (
              <button
                key={action}
                type="button"
                disabled={busy}
                onClick={() => review(action)}
                className="rounded border border-slate-400 px-4 py-2 hover:bg-slate-100 disabled:opacity-50"
              >
                {label}
              </button>
            ))}
          </div>
        </section>
      )}

      {detail && detail.audit.length > 0 && (
        <section aria-labelledby="audit" className="rounded border border-slate-200 bg-white p-4">
          <h2 id="audit" className="font-semibold">
            Audit log
          </h2>
          <table className="mt-2 w-full text-left text-sm">
            <thead>
              <tr>
                <th scope="col">When</th>
                <th scope="col">Who</th>
                <th scope="col">What</th>
                <th scope="col">Pack</th>
              </tr>
            </thead>
            <tbody>
              {detail.audit.map((a) => (
                <tr key={a.seq} className="border-t border-slate-100">
                  <td>{a.at.slice(11, 19)}</td>
                  <td>{a.actor}</td>
                  <td>
                    {a.action}: {a.detail}
                  </td>
                  <td className="font-mono">{a.pack_hash?.slice(0, 10) ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {detail?.outcome && (
        <p className="text-sm text-slate-600">
          {detail.outcome.totals.calls} model calls · {detail.outcome.totals.input_tokens} input tokens · $
          {detail.outcome.totals.cost_usd?.toFixed(4)} · {detail.outcome.elapsed_s}s · run mode {run?.mode}
        </p>
      )}
    </div>
  );
}
