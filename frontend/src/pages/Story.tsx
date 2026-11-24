// The guided story (decision 0016): one real request, what a careless answer would send, what the desk does,
// and the visitor's decision. Every number and every passage on this page comes from the API: the naive copy
// is the output of the real broken versions, and the desk run is a replay of a recorded live run.

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, type ForeignRecord, type RunDetail, type Story } from "../api";

const RUN_KEY = "a15-story-run";

const SYSTEM_LABELS: Record<string, string> = {
  crm: "Customer file",
  orders: "Orders",
  helpdesk: "Helpdesk",
  weblogs: "Website logs",
};

const STAGES = [
  ["INTAKE_DONE", "Read the request"],
  ["IDENTITY_VERIFIED", "Check who is asking"],
  ["DISCOVERY_DONE", "Find the records"],
  ["REDACTION_DONE", "Hide other people"],
  ["DRAFT_READY", "Write the reply"],
  ["VALIDATED", "Check the reply"],
  ["AWAITING_APPROVAL", "Wait for a person"],
] as const;

type Step = { name: string; tool?: string; status?: string; to_state?: string; rules?: string };

function highlight(text: string, terms: string[], mark: string): ReactNode[] {
  const found = terms.filter((t) => t && text.includes(t));
  if (found.length === 0) return [text];
  const pattern = new RegExp(`(${found.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "g");
  return text.split(pattern).map((part, i) =>
    found.includes(part) ? (
      <mark key={i} className={mark}>
        {part}
      </mark>
    ) : (
      part
    ),
  );
}

function euros(cents: unknown): string {
  return typeof cents === "number" ? `€${(cents / 100).toFixed(2)}` : "";
}

function StepTitle({ n, id, children }: { n: number; id: string; children: ReactNode }) {
  return (
    <h2 id={id} className="flex items-baseline gap-3 text-2xl font-bold">
      <span aria-hidden="true" className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-indigo-900 text-lg text-white">
        {n}
      </span>
      <span>
        <span className="sr-only">Step {n}: </span>
        {children}
      </span>
    </h2>
  );
}

function Email({ from, date, subject, text, gloss }: { from: string; date: string; subject: string; text: string; gloss?: string }) {
  return (
    <article className="rounded-lg border border-slate-300 bg-white shadow-sm">
      <p className="border-b border-slate-200 px-4 py-2 text-sm text-slate-600">
        From <strong className="text-slate-900">{from}</strong> · {date} · <em>{subject}</em>
      </p>
      <p className="whitespace-pre-wrap px-4 py-3">{text}</p>
      {gloss && <p className="border-t border-slate-100 px-4 py-2 text-sm italic text-slate-600">In English: {gloss}</p>}
    </article>
  );
}

function OrderCard({ r }: { r: ForeignRecord }) {
  const f = r.fields;
  return (
    <div className="rounded-lg border-2 border-red-300 bg-white p-4">
      <p className="text-sm font-semibold uppercase tracking-wide text-red-800">Another customer's order</p>
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        <dt className="text-slate-600">Order</dt>
        <dd className="font-mono">{r.record_id}</dd>
        <dt className="text-slate-600">Name</dt>
        <dd>
          <mark className="bg-red-100 px-0.5 text-red-900">{String(f.ship_name ?? r.owner ?? "")}</mark>
        </dd>
        <dt className="text-slate-600">Address</dt>
        <dd>
          <mark className="bg-red-100 px-0.5 text-red-900">
            {String(f.ship_street ?? "")}, {String(f.ship_postcode ?? "")} {String(f.ship_city ?? "")}
          </mark>
        </dd>
        <dt className="text-slate-600">Spent</dt>
        <dd>{euros(f.total_cents)}</dd>
        <dt className="text-slate-600">Placed</dt>
        <dd>{String(f.placed_at ?? "").slice(0, 10)}</dd>
      </dl>
    </div>
  );
}

function VisitsCard({ visits }: { visits: ForeignRecord[] }) {
  return (
    <div className="rounded-lg border-2 border-red-300 bg-white p-4">
      <p className="text-sm font-semibold uppercase tracking-wide text-red-800">
        {visits.length} page views by someone else at home
      </p>
      <p className="mt-1 text-sm text-slate-600">Same internet connection, their own browser, never logged in.</p>
      <ul tabIndex={0} className="mt-2 max-h-40 overflow-auto font-mono text-xs">
        {visits.map((v) => (
          <li key={v.record_id}>
            {String(v.fields.ts ?? "").replace("T", " ")} <mark className="bg-red-100 text-red-900">{String(v.fields.path ?? "")}</mark>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Check({ done, bad, children }: { done: boolean; bad?: boolean; children: ReactNode }) {
  const icon = !done ? "○" : bad ? "!" : "✓";
  const tone = !done ? "border-slate-200 text-slate-500" : bad ? "border-amber-400 bg-amber-50" : "border-green-300 bg-green-50";
  return (
    <li className={`flex gap-3 rounded-lg border p-3 transition-colors ${tone}`}>
      <span aria-hidden="true" className="w-5 shrink-0 text-center font-bold">
        {icon}
      </span>
      <div className="text-sm">{children}</div>
    </li>
  );
}

export default function StoryPage() {
  const [story, setStory] = useState<Story | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(() => sessionStorage.getItem(RUN_KEY));
  const [steps, setSteps] = useState<Step[]>([]);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.story().then(setStory).catch((e: Error) => setError(e.message));
  }, []);

  const load = useCallback(
    (id: string) =>
      api
        .run(id)
        .then(setDetail)
        .catch(() => {
          sessionStorage.removeItem(RUN_KEY); // the sandbox expired: start again
          setRunId(null);
        }),
    [],
  );

  useEffect(() => {
    if (!runId) return;
    setSteps([]);
    load(runId);
    const es = new EventSource(`/api/runs/${runId}/events`);
    es.addEventListener("step", (e) => {
      const s = JSON.parse((e as MessageEvent).data) as Step;
      if (s.name === "case.transition" || s.name === "tool.complete") setSteps((prev) => [...prev, s]);
    });
    es.addEventListener("done", () => {
      es.close();
      load(runId);
    });
    es.onerror = () => es.close();
    return () => es.close();
  }, [runId, load]);

  async function runDesk() {
    setBusy(true);
    setError(null);
    try {
      const { run_id } = await api.start({ source: "story" });
      sessionStorage.setItem(RUN_KEY, run_id);
      setDetail(null);
      setRunId(run_id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function decide(action: "approve" | "reject") {
    if (!runId) return;
    setBusy(true);
    setError(null);
    try {
      await api.review(runId, { action, note:
          action === "reject"
            ? "Après relecture, nous ne pouvons pas vous envoyer la copie en l'état ; nous revenons vers vous rapidement."
            : "" });
      await load(runId);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!story)
    return (
      <p role={error ? "alert" : "status"} className="text-slate-700">
        {error ?? "Loading the story…"}
      </p>
    );

  const n = story.naive;
  const orders = n.foreign_records.filter((r) => r.system === "orders");
  const visits = n.foreign_records.filter((r) => r.system === "weblogs");
  const mention = n.readable_mentions[0];
  const mentionTerms = n.readable_mentions.filter((m) => m.record_id === mention?.record_id).map((m) => m.text);
  const other = orders[0]?.owner ?? "another customer";
  const first = story.subject.split(" ")[0];

  const reached = (state: string) => steps.some((s) => s.name === "case.transition" && s.status === "ok" && s.to_state === state);
  const slips = steps.filter((s) => s.name === "tool.complete" && s.tool === "case_submit_redaction" && s.status === "refused").length;
  const probeRefused = steps.some((s) => s.name === "case.transition" && s.to_state === "APPROVED" && s.status === "refused");
  const finished = detail?.run.status === "done" || detail?.run.status === "failed";
  const refusal = detail?.tools.find((t) => t.tool === "case_submit_redaction" && !t.ok)?.text;
  const finalMessage = detail?.pack?.records.find((r) => r.record_id === mention?.record_id)?.fields[mention?.field ?? ""];
  const state = detail?.case?.state;
  const foreignInCopy = detail?.pack ? detail.pack.records.length - story.records_total : null;

  return (
    <div className="space-y-14">
      <section aria-labelledby="hero" className="rounded-2xl bg-gradient-to-br from-indigo-950 to-indigo-800 px-6 py-10 text-white md:px-10">
        <h1 id="hero" className="max-w-3xl text-3xl font-bold leading-tight md:text-4xl">
          A customer asks for all their data. Can AI answer without leaking someone else's?
        </h1>
        <p className="mt-4 max-w-3xl text-lg text-indigo-100">
          Under the GDPR, anyone can ask a company for a copy of every piece of personal data it holds on them
          (Article 15). The company has one month to reply, and the copy must not expose other people. That is hard:
          customer data is scattered across systems and tangled up with other people's.
        </p>
        <p className="mt-3 max-w-3xl text-indigo-100">
          Article 15 is a desk of AI agents that does this work, with code checks the AI can't get round and a person
          who approves every reply. Follow one request through it, in four steps.
        </p>
        <ol className="mt-6 flex flex-wrap gap-2 text-sm">
          {[
            ["#step-1", "1 · The request"],
            ["#step-2", "2 · The careless answer"],
            ["#step-3", "3 · The desk at work"],
            ["#step-4", "4 · Your call"],
          ].map(([href, label]) => (
            <li key={href}>
              <a href={href} className="inline-block rounded-full bg-white/10 px-3 py-1 hover:bg-white/20">
                {label}
              </a>
            </li>
          ))}
        </ol>
      </section>

      {error && (
        <p role="alert" className="rounded border border-red-300 bg-red-50 p-3 text-red-800">
          {error}
        </p>
      )}

      {/* 1. The request */}
      <section id="step-1" aria-labelledby="step-1-title" className="space-y-5 scroll-mt-4">
        <StepTitle n={1} id="step-1-title">
          {first} asks for a copy of their data
        </StepTitle>
        <p className="max-w-3xl text-slate-700">
          {story.subject} is a customer of Ferlane, a fictional French home-decor shop. They send a two-line email.
          The reply is due on <strong>{story.due}</strong>.
        </p>
        <Email
          from={story.request.sender}
          date={story.request.received_on}
          subject={story.request.subject_line}
          text={story.request.text}
          gloss={story.gloss.request}
        />
        <div>
          <h3 className="font-semibold">
            Their data is in four systems: {story.records_total} records
          </h3>
          <ul className="mt-2 grid grid-cols-2 gap-3 md:grid-cols-4">
            {Object.entries(story.records_by_system).map(([sys, count]) => (
              <li key={sys} className="rounded-lg border border-slate-200 bg-white p-3">
                <p className="text-2xl font-bold">{count}</p>
                <p className="text-sm text-slate-600">{SYSTEM_LABELS[sys] ?? sys}</p>
              </li>
            ))}
          </ul>
        </div>
        <div className="rounded-lg border border-amber-300 bg-amber-50 p-4">
          <h3 className="font-semibold text-amber-950">Mixed in with them, three traps</h3>
          <ol className="mt-2 list-decimal space-y-1 pl-5 text-amber-950">
            {visits.length > 0 && (
              <li>
                <strong>Someone else at home.</strong> {visits.length} page views came from the same internet
                connection, by another person with their own browser.
              </li>
            )}
            {orders.length > 0 && (
              <li>
                <strong>Another customer's order.</strong> {other}'s parcel was delivered to {first} by mistake. That
                order, with {other}'s name and address, is in the order system.
              </li>
            )}
            {mention && (
              <li>
                <strong>Someone else's details inside {first}'s own message.</strong> {first} told the helpdesk
                about the parcel. The message belongs in {first}'s copy; {other}'s name and order number don't.
              </li>
            )}
          </ol>
        </div>
      </section>

      {/* 2. The careless answer */}
      <section id="step-2" aria-labelledby="step-2-title" className="space-y-5 scroll-mt-4">
        <StepTitle n={2} id="step-2-title">
          The careless answer: find everything that matches, send it all
        </StepTitle>
        <p className="max-w-3xl text-lg">
          {first}'s copy would hold <strong>{n.records_total} records</strong>.{" "}
          <strong className="text-red-800">{n.foreign_records.length} of them are about other people</strong>
          {n.readable_mentions.length > 0 && (
            <>
              , and <strong className="text-red-800">{n.readable_mentions.length} passages</strong> name another
              customer
            </>
          )}
          .
        </p>
        <div className="grid gap-4 md:grid-cols-2">
          {orders.map((r) => (
            <OrderCard key={r.record_id} r={r} />
          ))}
          {visits.length > 0 && <VisitsCard visits={visits} />}
        </div>
        {mention && (
          <div className="rounded-lg border-2 border-red-300 bg-white p-4">
            <p className="text-sm font-semibold uppercase tracking-wide text-red-800">{first}'s own message, sent as it is</p>
            <p className="mt-2 whitespace-pre-wrap">{highlight(mention.context, mentionTerms, "bg-red-100 px-0.5 text-red-900")}</p>
            {story.gloss[mention.record_id] && (
              <p className="mt-2 text-sm italic text-slate-600">In English: {story.gloss[mention.record_id]}</p>
            )}
          </div>
        )}
        <p className="max-w-3xl text-slate-700">
          Sending this hands {other}'s name, home address and purchase, and a household member's browsing, to{" "}
          {first}. That is a personal-data breach, which the shop must assess and may have to report to the regulator
          within 72 hours (GDPR Article 33).
        </p>
        <details className="max-w-3xl text-sm text-slate-600">
          <summary className="cursor-pointer font-medium text-slate-800">How was this careless answer made?</summary>
          <p className="mt-2">
            Nothing on this card is written by hand. It is the desk's own output on this request, with two of its
            safeguards switched off: the test versions{" "}
            {n.broken_versions.map((b, i) => (
              <span key={b}>
                {i > 0 && " and "}
                <code>{b}</code>
              </span>
            ))}
            ,
            which the eval suite uses to prove its checks can fail. Even then, a later check noticed that the
            letter's record count did not match the copy and stopped the reply before approval: the desk has more
            than one line of defence.
          </p>
        </details>
      </section>

      {/* 3. The desk at work */}
      <section id="step-3" aria-labelledby="step-3-title" className="space-y-5 scroll-mt-4">
        <StepTitle n={3} id="step-3-title">
          The desk at work
        </StepTitle>
        <p className="max-w-3xl text-slate-700">
          {story.mode === "replay" ? (
            <>
              Now the real desk on the same request: an AI agent ({story.recorded.model}) with tools for the four
              systems, inside code checks it cannot talk its way past. This is a replay of a recorded live run: the
              model's real answers, at no cost to you.
            </>
          ) : (
            <>Now the real desk on the same request, played by the scripted agent (no recording is available).</>
          )}
        </p>
        {!runId && (
          <button
            type="button"
            disabled={busy}
            onClick={runDesk}
            className="rounded-lg bg-indigo-700 px-6 py-3 text-lg font-semibold text-white shadow hover:bg-indigo-800 disabled:bg-slate-400"
          >
            Run the desk on {first}'s request
          </button>
        )}
        {runId && (
          <div className="grid gap-6 md:grid-cols-[16rem_1fr]">
            <ol aria-label="Progress" className="space-y-1 text-sm">
              {STAGES.map(([s, label]) => (
                <li key={s} className={`flex items-center gap-2 ${reached(s) ? "text-slate-900" : "text-slate-400"}`}>
                  <span aria-hidden="true">{reached(s) ? "●" : "○"}</span>
                  {label}
                  <span className="sr-only">{reached(s) ? " (done)" : " (not yet)"}</span>
                </li>
              ))}
            </ol>
            <ul aria-live="polite" className="space-y-2">
              <Check done={reached("IDENTITY_VERIFIED")}>
                <strong>Is it really {first}?</strong> The email came from the address on the account: identity
                verified. An impostor would be asked for proof first.
              </Check>
              <Check done={reached("DISCOVERY_DONE")}>
                <strong>Traps 1 and 2 never reach the AI.</strong> Once {first} is verified, the company systems only
                return records linked to that account. The agent found all {story.records_total} of {first}'s records; the household's
                page views and {other}'s order were never shown to it.
              </Check>
              {(slips > 0 || (finished && reached("REDACTION_DONE"))) && (
                <Check done={slips > 0 || reached("REDACTION_DONE")} bad={slips > 0}>
                  {slips > 0 ? (
                    <>
                      <strong>Trap 3: the AI slipped, and the code caught it.</strong> It hid {other}'s name but left
                      the order number readable. A redaction check refused the submission and said why:
                      {refusal && (
                        <blockquote className="mt-2 border-l-4 border-amber-400 pl-3 font-mono text-xs">
                          {refusal.replace(/^refused: /, "").slice(0, 240)}…
                        </blockquote>
                      )}
                    </>
                  ) : (
                    <>
                      <strong>Trap 3: hidden.</strong> {other}'s name and order number were hidden on the first try.
                    </>
                  )}
                </Check>
              )}
              {reached("REDACTION_DONE") && (
                <Check done>
                  <strong>Fixed{slips > 0 ? " on the next try" : ""}.</strong> The check verified that nothing about
                  anyone else is still readable.
                  {typeof finalMessage === "string" && (
                    <span className="mt-2 block whitespace-pre-wrap rounded bg-white p-2 text-slate-800">
                      {highlight(finalMessage, ["[masqué : tiers]"], "bg-green-100 px-0.5 text-green-900")}
                    </span>
                  )}
                </Check>
              )}
              {reached("VALIDATED") && (
                <Check done>
                  <strong>The reply is checked against the copy.</strong> The letter gives every item the law requires
                  (purposes, recipients, how long data is kept, the customer's rights), and its claims match the copy.
                </Check>
              )}
              {probeRefused && (
                <Check done>
                  <strong>Nothing leaves without a person.</strong> As a test, the desk tried to send the reply with no
                  approval; the release guard refused.
                </Check>
              )}
            </ul>
          </div>
        )}
        {finished && detail?.run.status === "failed" && (
          <p role="alert" className="rounded border border-red-300 bg-red-50 p-3 text-red-800">
            The run failed ({detail.run.error ?? "unknown error"}).{" "}
            <button type="button" className="underline" onClick={runDesk}>
              Run it again
            </button>
          </p>
        )}
        {finished && story.recorded.elapsed_s && story.mode === "replay" && (
          <p className="text-sm text-slate-600">
            The live run took {Math.round(story.recorded.elapsed_s)} s, {story.recorded.model_calls} model calls and $
            {story.recorded.cost_usd?.toFixed(4)}.
          </p>
        )}
      </section>

      {/* 4. Your call */}
      <section id="step-4" aria-labelledby="step-4-title" className="space-y-5 scroll-mt-4">
        <StepTitle n={4} id="step-4-title">
          Your call
        </StepTitle>
        {!detail?.pack && <p className="text-slate-600">Run the desk first: the reply will wait here for you.</p>}
        {detail?.pack && (
          <>
            <p className="max-w-3xl text-lg">
              {first}'s copy: <strong>{detail.pack.records.length} records</strong>
              {foreignInCopy === 0 ? `, all of them ${first}'s,` : ","} with{" "}
              <strong>{detail.pack.redactions.length} passages hidden</strong>. You are the reviewer: nothing is sent
              until you approve.
            </p>
            {detail.case?.letter && (
              <details className="max-w-3xl rounded-lg border border-slate-200 bg-white p-4">
                <summary className="cursor-pointer font-medium">Read the reply letter (French)</summary>
                <p className="mt-2 font-medium">{detail.case.letter.subject_line}</p>
                <pre tabIndex={0} className="mt-1 max-h-80 overflow-auto whitespace-pre-wrap font-sans text-sm">
                  {detail.case.letter.body}
                </pre>
              </details>
            )}
            {detail.can_review && (
              <div className="flex flex-wrap gap-3">
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => decide("approve")}
                  className="rounded-lg bg-green-700 px-6 py-3 text-lg font-semibold text-white hover:bg-green-800 disabled:bg-slate-400"
                >
                  Approve and send
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => decide("reject")}
                  className="rounded-lg border border-slate-400 px-6 py-3 text-lg hover:bg-slate-100 disabled:opacity-50"
                >
                  Reject
                </button>
              </div>
            )}
            {state === "CLOSED" && (
              <div role="status" className="max-w-3xl rounded-lg border-2 border-green-600 bg-green-50 p-4">
                <p className="text-lg font-semibold text-green-900">Sent to {first}.</p>
                <p className="mt-1 text-sm text-green-900">
                  The audit log records your approval, signed over this exact copy
                  {detail.audit.find((a) => a.action === "approve")?.pack_hash && (
                    <>
                      {" "}
                      (<span className="font-mono">{detail.audit.find((a) => a.action === "approve")?.pack_hash?.slice(0, 12)}</span>)
                    </>
                  )}
                  , then the release. If the copy had changed after you approved, the release would have been refused.
                </p>
              </div>
            )}
            {state === "REJECTED" && (
              <p role="status" className="max-w-3xl rounded-lg border border-slate-300 bg-white p-4">
                Rejected: nothing was sent, and the case is closed with your reason in the audit log.
              </p>
            )}
          </>
        )}
      </section>

      <section aria-labelledby="more" className="rounded-2xl border border-slate-200 bg-white p-6">
        <h2 id="more" className="text-xl font-bold">
          Go further
        </h2>
        <ul className="mt-3 grid gap-3 md:grid-cols-2">
          <li>
            <Link className="font-medium text-indigo-700 underline" to="/">
              The whole guard layer, with numbers
            </Link>{" "}
            <span className="text-slate-600">from every recorded live run, and guards on against guards off.</span>
          </li>
          {runId && (
            <li>
              <Link className="font-medium text-indigo-700 underline" to={`/runs/${runId}`}>
                Every step and tool call of this run
              </Link>{" "}
              <span className="text-slate-600">and its trace, with timings and the rules that fired.</span>
            </li>
          )}
          <li>
            <Link className="font-medium text-indigo-700 underline" to="/break">
              Try to break it
            </Link>{" "}
            <span className="text-slate-600">with impostors, fake lawyers and prompt injections.</span>
          </li>
          <li>
            <Link className="font-medium text-indigo-700 underline" to="/evals">
              Check the numbers
            </Link>{" "}
            <span className="text-slate-600">on 160 labelled requests, with hard safety gates.</span>
          </li>
          <li>
            <Link className="font-medium text-indigo-700 underline" to="/try">
              Run another request
            </Link>{" "}
            <span className="text-slate-600">from Ferlane's other customers.</span>
          </li>
        </ul>
        {runId && (
          <button
            type="button"
            className="mt-4 text-sm text-slate-600 underline"
            onClick={() => {
              sessionStorage.removeItem(RUN_KEY);
              setRunId(null);
              setDetail(null);
              setSteps([]);
            }}
          >
            Start the story again
          </button>
        )}
      </section>
    </div>
  );
}
