// The first page (decision 0017): the guard layer around the model, and what it did on recorded live runs.
// Every number comes from /api/harness, which is built from the live eval runs by `a15 eval harness`.

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, KIND_LABELS, type Catch, type Harness } from "../api";

const SOURCES = {
  gdpr: "https://eur-lex.europa.eu/eli/reg/2016/679/oj",
  edpb: "https://www.edpb.europa.eu/documents/coordinated-enforcement-framework/coordinated-enforcement-action-implementation-of-the_en",
  gartner: "https://www.datagrail.io/resources/interactive/data-privacy-trends/methodology/",
};

const SCENARIO_LABELS: Record<string, string> = {
  measured: "DeepSeek Flash, off-peak (as measured)",
  peak: "DeepSeek Flash, peak hours",
  bigger: "DeepSeek V4 Pro, peak hours",
  eu: "Mistral Large, EU endpoint",
};

function usd(v: number): string {
  const digits = v < 0.01 ? 4 : v < 1 ? 3 : 2;
  return "$" + v.toFixed(digits);
}

// What a request let through with the guards off, from the most to the least serious.
const CATEGORIES: [string, string, string][] = [
  ["leak_missed", "another person's details left readable, and no guard saw it", "border-red-300 bg-red-50"],
  ["leak_caught", "another person's details left readable; the guards catch it", "border-red-200 bg-white"],
  ["procedure", "a request that also asked for erasure, prepared without a person deciding (R-EXCESSIVE)", "border-amber-200 bg-white"],
  ["harmless", "nothing leaked: the model left notes in its list of unresolved mentions, which the guard treats as unfinished", "border-slate-200 bg-white"],
];

function Num({ value, bad }: { value: number; bad?: boolean }) {
  return <span className={`font-mono text-2xl font-bold ${bad && value > 0 ? "text-red-700" : "text-green-800"}`}>{value}</span>;
}

function Example({ c }: { c: Catch }) {
  return (
    <li className="rounded border border-slate-200 bg-slate-50 p-3 text-sm">
      <p className="text-xs text-slate-600">
        {c.case_id} · {KIND_LABELS[c.kind] ?? c.kind} · <span className="font-mono">{c.tool}</span>
      </p>
      <p className="mt-1">
        <span className="font-semibold">The model tried:</span> <span className="break-all font-mono text-xs">{c.attempt}</span>
      </p>
      <p className="mt-1">
        <span className="font-semibold text-amber-900">Code refused:</span> <span className="font-mono text-xs">{c.refusal}</span>
      </p>
    </li>
  );
}

// The most telling examples first: a person's data or an injection over a malformed form.
function pick(examples: Catch[]): Catch[] {
  const score = (c: Catch) =>
    (/readable|another customer|not confirmed/.test(c.refusal + c.attempt) ? -2 : 0) + (c.kind.startsWith("adversarial") ? -1 : 0);
  const seen = new Set<string>();
  return [...examples]
    .sort((a, b) => score(a) - score(b))
    .filter((c) => {
      const key = `${c.case_id}${c.tool}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .slice(0, 3);
}

export default function HarnessPage() {
  const [h, setH] = useState<Harness | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>("redaction");

  useEffect(() => {
    api.harness().then(setH).catch((e: Error) => setError(e.message));
  }, []);

  if (!h)
    return (
      <p role={error ? "alert" : "status"} className="text-slate-700">
        {error ?? "Loading…"}
      </p>
    );

  const c = h.catches;
  const e2 = h.e2;
  const cost = h.cost;
  const releaseProbes = c.by_guard.release?.count ?? 0;
  const sc = h.scaling;
  const bigCase = sc?.rows.find((r) => r.measured?.case_id);

  return (
    <div className="space-y-14">
      {/* The claim */}
      <section aria-labelledby="hero" className="rounded-2xl bg-gradient-to-br from-slate-950 to-indigo-900 px-6 py-10 text-white md:px-10">
        <p className="text-sm font-semibold uppercase tracking-wider text-indigo-300">Article 15 · a guard layer for an LLM agent</p>
        <h1 id="hero" className="mt-2 max-w-3xl text-3xl font-bold leading-tight md:text-4xl">
          The model makes mistakes. The code around it doesn't let them through.
        </h1>
        <p className="mt-4 max-w-3xl text-lg text-indigo-100">
          This desk answers GDPR access requests with a cheap language model ({cost.model}). Around the model sits a
          layer of ordinary code: it decides what the model can see, which step comes next, and whether a result is
          acceptable. Below is what that layer did on {c.cases} recorded live runs ({c.distinct_requests} different
          requests), and what got through when it was switched off.
        </p>
        <dl className="mt-8 grid gap-4 sm:grid-cols-3">
          <div className="rounded-lg bg-white/10 p-4">
            <dt className="text-sm text-indigo-200">times code refused the model</dt>
            <dd className="text-4xl font-bold">{c.model_refusals}</dd>
            <dd className="text-sm text-indigo-200">
              in {c.cases_with_a_refusal} of {c.cases} live runs
            </dd>
          </div>
          {e2 && (
            <div className="rounded-lg bg-white/10 p-4">
              <dt className="text-sm text-indigo-200">requests reaching a person with a problem, guards off</dt>
              <dd className="text-4xl font-bold">{e2.shadow.cases_with_violation_at_approval}</dd>
              <dd className="text-sm text-indigo-200">
                with them on: {e2.guarded.cases_with_violation_at_approval}, same {e2.shadow.cases} requests
              </dd>
            </div>
          )}
          <div className="rounded-lg bg-white/10 p-4">
            <dt className="text-sm text-indigo-200">attempts to send without a person</dt>
            <dd className="text-4xl font-bold">{releaseProbes}</dd>
            <dd className="text-sm text-indigo-200">all refused</dd>
          </div>
        </dl>
      </section>

      {/* Why it matters */}
      <section aria-labelledby="why" className="space-y-4">
        <h2 id="why" className="text-2xl font-bold">
          Why a guard layer, and not a better prompt
        </h2>
        <div className="grid gap-4 md:grid-cols-3">
          <div className="rounded-lg border border-slate-200 bg-white p-4">
            <h3 className="font-semibold">The job is high-stakes and frequent</h3>
            <p className="mt-1 text-sm text-slate-700">
              Anyone can ask a company for all their personal data. It must answer within one month (
              <a className="underline" href={SOURCES.gdpr}>
                GDPR Art. 12(3)
              </a>
              ). Access is "one of the most frequently exercised" rights, and in 2024 30 European regulators examined
              1,185 organisations on how they handle it (
              <a className="underline" href={SOURCES.edpb}>
                EDPB, January 2025
              </a>
              ).
            </p>
          </div>
          <div className="rounded-lg border border-slate-200 bg-white p-4">
            <h3 className="font-semibold">One slip is a data breach</h3>
            <p className="mt-1 text-sm text-slate-700">
              The copy must not expose anyone else (Art. 15(4)). Customer data is tangled with other people's: a
              household's shared connection, a parcel delivered to the wrong person, a name in a support ticket.
              Sending one of those is a breach to assess and possibly report within 72 hours (Art. 33).
            </p>
          </div>
          <div className="rounded-lg border border-slate-200 bg-white p-4">
            <h3 className="font-semibold">Doing it by hand is expensive</h3>
            <p className="mt-1 text-sm text-slate-700">
              Gartner estimates about $1,524 to process one request manually (Market Guide for Subject Rights Request
              Automation, 2023,{" "}
              <a className="underline" href={SOURCES.gartner}>
                as cited by DataGrail
              </a>
              , a vendor of such tools). A model is cheap, but a prompt is a request, not a guarantee.
            </p>
          </div>
        </div>
      </section>

      {/* E2 */}
      {e2 && (
        <section aria-labelledby="e2" className="space-y-4">
          <h2 id="e2" className="text-2xl font-bold">
            Same model, same {e2.shadow.cases} requests: guards on and off
          </h2>
          <p className="max-w-3xl text-slate-700">
            Experiment E2. In the "off" run, every guard still checks but only writes down what it would have refused,
            and lets it through. The rules are still in the model's prompt in both runs. Access stays scoped to the
            verified person in both, so this measures the checks, not the scoping.
          </p>
          {e2.shadow.cases < e2.cases_planned && (
            <p role="note" className="max-w-3xl rounded border border-amber-300 bg-amber-50 p-2 text-sm text-amber-950">
              Incomplete: the guards-off run has covered {e2.shadow.cases} of the {e2.cases_planned} test requests so
              far, so both columns compare those {e2.shadow.cases}.
            </p>
          )}
          <table className="w-full max-w-3xl border-collapse text-left">
            <caption className="sr-only">Experiment E2: results with the guards off and on</caption>
            <thead>
              <tr className="border-b border-slate-300 text-sm text-slate-600">
                <th scope="col" className="py-2 font-medium">
                  Across {e2.shadow.cases} live requests
                </th>
                <th scope="col" className="py-2 text-center font-medium">
                  Guards off (prompt only)
                </th>
                <th scope="col" className="py-2 text-center font-medium">
                  Guards on
                </th>
              </tr>
            </thead>
            <tbody>
              <tr className="border-b border-slate-200">
                <th scope="row" className="py-2 font-normal">
                  requests reaching the approval step with a problem
                </th>
                <td className="text-center">
                  <Num value={e2.shadow.cases_with_violation_at_approval} bad />
                </td>
                <td className="text-center">
                  <Num value={e2.guarded.cases_with_violation_at_approval} bad />{" "}
                  <span className="text-xs text-slate-600">({e2.guarded.violations_refused} refused)</span>
                </td>
              </tr>
              {e2.rows.map((r) => (
                <tr key={r.key} className="border-b border-slate-200">
                  <th scope="row" className="py-2 font-normal">
                    {r.label}
                  </th>
                  <td className="text-center">
                    <Num value={e2.shadow[r.key as keyof typeof e2.shadow] as number} bad />
                  </td>
                  <td className="text-center">
                    <Num value={e2.guarded[r.key as keyof typeof e2.guarded] as number} bad />
                  </td>
                </tr>
              ))}
              <tr>
                <th scope="row" className="py-2 font-normal">
                  requests handled fully correctly
                </th>
                <td className="text-center font-mono text-lg">
                  {e2.shadow.passed}/{e2.shadow.cases}
                </td>
                <td className="text-center font-mono text-lg">
                  {e2.guarded.passed}/{e2.guarded.cases}
                </td>
              </tr>
            </tbody>
          </table>
          {e2.let_through.length > 0 && (
            <div className="max-w-3xl space-y-2">
              <h3 className="font-semibold">What got through with the guards off, one request at a time</h3>
              <ul className="space-y-2 text-sm">
                {CATEGORIES.map(([cat, label, tone]) => {
                  const items = e2.let_through.filter((x) => x.category === cat);
                  if (items.length === 0) return null;
                  return (
                    <li key={cat} className={`rounded-lg border p-3 ${tone}`}>
                      <p className="font-medium">
                        {items.length} × {label}
                      </p>
                      <p className="mt-1 text-xs">
                        {items.map((x) => `${x.case_id} (${KIND_LABELS[x.kind] ?? x.kind})`).join(" · ")}
                      </p>
                      {cat === "leak_missed" && (
                        <p className="mt-2">
                          <strong>Found by this experiment, then fixed.</strong> The redaction check only looked at
                          what the model chose to hide, and at order numbers. A new check now refuses any email address
                          or phone number left readable that is not one of the customer's verified ones. Replaying the
                          recorded run with it, it now flags{" "}
                          {items
                            .filter((x) => x.now_flagged && x.now_flagged.length > 0)
                            .map((x) => x.case_id)
                            .join(", ") || "nothing yet"}
                          . It errs on the safe side: a customer's own second address that was never confirmed is hidden
                          too.
                        </p>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
          <p className="max-w-3xl text-xs text-slate-600">
            {cost.model}, one agent, thinking off. Guards-on run: {e2.guarded.run}
            {e2.guarded.reruns && e2.guarded.reruns.length > 0 && ` (cases ${e2.guarded.reruns.join(", ")} re-run after a check was added: the order-number check, then the email and phone check)`}
            ; guards-off run: {e2.shadow.run}. One run of each; model answers vary from run to run.
          </p>
        </section>
      )}

      {/* The guard layer */}
      <section aria-labelledby="layer" className="space-y-4">
        <h2 id="layer" className="text-2xl font-bold">
          The guard layer, and what each guard caught
        </h2>
        <p className="max-w-3xl text-slate-700">
          Each rule is written once in <code>policy.yaml</code>: it is rendered into the model's prompt and enforced
          by the code below. Counts are from {c.cases} recorded live runs of {c.distinct_requests} different requests,{" "}
          {c.tool_calls.toLocaleString("en")} tool calls in all. A refusal tells the model why, and the model usually fixes it on the next call.
        </p>
        <ol className="space-y-2">
          {h.guards.map((g, i) => {
            const stats = c.by_guard[g.id] ?? { count: 0, cases: 0, examples: [] };
            const expanded = open === g.id;
            return (
              <li key={g.id} className="rounded-lg border border-slate-200 bg-white">
                <button
                  type="button"
                  aria-expanded={expanded}
                  onClick={() => setOpen(expanded ? null : g.id)}
                  className="flex w-full items-start gap-4 p-4 text-left"
                >
                  <span aria-hidden="true" className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-indigo-900 text-sm text-white">
                    {i + 1}
                  </span>
                  <span className="flex-1">
                    <span className="block font-semibold">{g.title}</span>
                    <span className="block text-sm text-slate-700">{g.what}</span>
                    <span className="mt-1 block text-xs text-slate-500">
                      {g.rules.length > 0 && <>{g.rules.join(", ")} · </>}
                      {g.code}
                    </span>
                  </span>
                  <span className="shrink-0 text-right">
                    <span className="block text-2xl font-bold">{stats.count}</span>
                    <span className="block text-xs text-slate-600">
                      {g.id === "release" ? "attempts refused" : `refusals, in ${stats.cases} runs`}
                    </span>
                  </span>
                </button>
                {expanded && stats.examples.length > 0 && (
                  <ul className="space-y-2 border-t border-slate-100 p-4">
                    {pick(stats.examples).map((x, j) => (
                      <Example key={j} c={x} />
                    ))}
                  </ul>
                )}
                {expanded && stats.examples.length === 0 && (
                  <p className="border-t border-slate-100 p-4 text-sm text-slate-600">
                    Not triggered in these runs: the model never tried it. The eval suite's broken versions prove this
                    guard does refuse when it should.
                  </p>
                )}
              </li>
            );
          })}
        </ol>
        {(c.by_guard.repeat?.count ?? 0) > 0 && (
          <p className="text-xs text-slate-600">
            Plus {c.by_guard.repeat.count} harmless refusals of a step already done (asking to confirm an address that
            was already confirmed), counted in the total above.
          </p>
        )}
      </section>

      {/* Cost */}
      <section aria-labelledby="cost" className="space-y-4">
        <h2 id="cost" className="text-2xl font-bold">
          What it costs here, and what it would cost for real
        </h2>
        <div className="grid gap-4 md:grid-cols-2">
          <div className="rounded-lg border border-slate-200 bg-white p-4">
            <h3 className="font-semibold">Measured, per request ({cost.cases} live requests)</h3>
            <dl className="mt-2 grid grid-cols-[1fr_auto] gap-y-1 text-sm">
              <dt>Model cost (DeepSeek Flash, off-peak)</dt>
              <dd className="font-mono">${cost.cost_per_case_usd.toFixed(4)}</dd>
              <dt>Model calls</dt>
              <dd className="font-mono">{cost.model_calls_per_case}</dd>
              <dt>Tool calls to company systems and the case</dt>
              <dd className="font-mono">{cost.tool_calls_per_case}</dd>
              <dt>Tokens read / written</dt>
              <dd className="font-mono">
                {Math.round(cost.input_tokens_per_case / 1000)}k / {Math.round(cost.output_tokens_per_case / 1000)}k
              </dd>
              <dt>Time, median</dt>
              <dd className="font-mono">{cost.elapsed_p50_s} s</dd>
            </dl>
            <p className="mt-2 text-xs text-slate-600">
              Then a person reviews the prepared reply and approves it. The guards are plain code and add milliseconds.
              The fictional customers are small: {sc ? `${sc.rows[0].records} records on average` : "a few dozen records"}.
            </p>
          </div>
          <div className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-950">
            <h3 className="font-semibold">Not measured: this is not a real company</h3>
            <ul className="mt-2 list-disc space-y-1 pl-5">
              <li>
                The four systems (CRM, orders, helpdesk, web logs) are simulated databases that answer in milliseconds.
                Real systems add their own latency to each of the ~{Math.round(cost.tool_calls_per_case)} tool calls,
                and rate limits.
              </li>
              <li>Real customers have longer histories: see the estimates below.</li>
              <li>Connecting real systems, hosting, and the reviewer's time are not counted.</li>
            </ul>
            <p className="mt-2">
              So the $ figures are the model's share only. Gartner's $1,524 is the whole manual process; the two are not
              directly comparable.
            </p>
          </div>
        </div>

        {sc && (
          <div className="space-y-3 rounded-lg border border-slate-200 bg-white p-4">
            <h3 className="font-semibold">How the model's cost grows with a customer's history</h3>
            <p className="max-w-3xl text-sm text-slate-700">
              Measured on {sc.cases} live requests: the cost grows in a straight line with the number of records, about{" "}
              <strong>${sc.cost_per_record_usd.toFixed(4)} per record</strong> on DeepSeek Flash off-peak. Each record
              adds about {(sc.input_tokens_per_record / 1000).toFixed(1)}k input tokens, because the agent re-reads its
              conversation on every call ({Math.round(sc.cache_hit_share * 100)}% of it served cheaply from the
              provider's cache). Below, that line is extended to bigger customers and priced at other settings with each
              provider's published prices.
            </p>
            <div className="overflow-x-auto" tabIndex={0}>
              <table className="w-full min-w-[40rem] border-collapse text-left text-sm">
                <caption className="sr-only">Model cost per request by size of the customer's history</caption>
                <thead>
                  <tr className="border-b border-slate-300 text-slate-600">
                    <th scope="col" className="py-2 pr-3 font-medium">
                      Records
                    </th>
                    <th scope="col" className="py-2 pr-3 font-medium">
                      Measured
                    </th>
                    {sc.scenarios.map((x) => (
                      <th key={x.id} scope="col" className="py-2 pr-3 font-medium">
                        {SCENARIO_LABELS[x.id] ?? x.model}
                        <span className="block text-xs font-normal">estimate</span>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sc.rows.map((r) => (
                    <tr key={r.records} className={`border-b border-slate-100 ${r.over_cap ? "text-slate-500" : ""}`}>
                      <th scope="row" className="py-2 pr-3 font-normal">
                        <span className="font-mono">{r.records.toLocaleString("en")}</span>
                        <span className="block text-xs text-slate-500">
                          {r.measured?.average
                            ? "the average case"
                            : r.measured?.case_id
                              ? `largest measured (${r.measured.case_id})`
                              : r.over_cap
                                ? "over today's limit"
                                : ""}
                        </span>
                      </th>
                      <td className="py-2 pr-3 font-mono">{r.measured ? usd(r.measured.cost_usd) : "—"}</td>
                      {sc.scenarios.map((x) => (
                        <td key={x.id} className={`py-2 pr-3 font-mono ${r.over_cap ? "italic" : ""}`}>
                          {usd(r.cost_usd[x.id])}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <ul className="max-w-3xl list-disc space-y-1 pl-5 text-sm text-slate-700">
              <li>
                <strong>Today's limit: about {sc.records_before_cap} records.</strong> Each request may use at most{" "}
                {(sc.input_token_cap / 1_000_000).toFixed(0)}M input tokens
                {bigCase?.measured?.input_tokens
                  ? `; the largest measured case (${bigCase.records} records) used ${Math.round(bigCase.measured.input_tokens / 1000)}k`
                  : ""}
                . A bigger request stops and goes to a person. The grey rows show what the same design would cost if the
                cap were raised; a real deployment would rather have code summarise bulky records (web logs, order lines)
                so the model reads only the free text that needs judgement. That is not built yet.
              </li>
              <li>
                The straight line is fitted mostly on small requests and runs a little low: compare the measured and
                estimated figures in the first two rows. Treat the larger rows as an order of magnitude.
              </li>
              <li>
                Caps are kill switches, not prices: a request stops at 60 model calls, 1M input tokens, 15 minutes or
                $0.30 (the eval runs used $0.05) and goes to a person, so a looping agent cannot run up a bill.
              </li>
            </ul>
          </div>
        )}
      </section>

      {/* Go further */}
      <section aria-labelledby="more" className="rounded-2xl border border-slate-200 bg-white p-6">
        <h2 id="more" className="text-xl font-bold">
          See it for yourself
        </h2>
        <ul className="mt-3 grid gap-3 md:grid-cols-2">
          <li>
            <Link className="font-medium text-indigo-700 underline" to="/story">
              Follow one request
            </Link>{" "}
            <span className="text-slate-600">
              in four steps: the traps, what a careless answer sends, and the moment a guard catches the model.
            </span>
          </li>
          <li>
            <Link className="font-medium text-indigo-700 underline" to="/break">
              Try to break it
            </Link>{" "}
            <span className="text-slate-600">with impostors, fake lawyers and prompt injections.</span>
          </li>
          <li>
            <Link className="font-medium text-indigo-700 underline" to="/evals">
              All the eval numbers
            </Link>{" "}
            <span className="text-slate-600">on 160 labelled requests, with hard safety gates.</span>
          </li>
          <li>
            <Link className="font-medium text-indigo-700 underline" to="/try">
              Run another request
            </Link>{" "}
            <span className="text-slate-600">and act as the reviewer.</span>
          </li>
        </ul>
      </section>
    </div>
  );
}
