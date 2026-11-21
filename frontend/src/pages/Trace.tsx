import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type Span } from "../api";

function Node({ span, children, depth }: { span: Span; children: Map<string, Span[]>; depth: number }) {
  const kids = children.get(span.span_id) ?? [];
  const a = span.attributes;
  const label =
    span.name === "agent.run"
      ? `agent ${a.agent}`
      : span.name === "tool.call"
        ? `tool ${a.tool}`
        : span.name === "llm.call"
          ? `model ${a.model ?? ""}`
          : span.name;
  const extra =
    span.name === "llm.call"
      ? ` · ${a.input_tokens ?? "?"} in / ${a.output_tokens ?? "?"} out · $${Number(a.cost_usd ?? 0).toFixed(5)}`
      : span.name === "tool.call"
        ? ` · ${a.status}`
        : "";
  return (
    <li>
      <span className={span.status === "ERROR" ? "text-amber-800" : ""}>
        {label}
        <span className="text-slate-500">
          {" "}
          · {span.duration_ms} ms{extra}
          {a["case.stage"] ? ` · ${a["case.stage"]}` : ""}
        </span>
      </span>
      {kids.length > 0 && (
        <ul className="ml-4 border-l border-slate-200 pl-3">
          {kids.map((k) => (
            <Node key={k.span_id} span={k} children={children} depth={depth + 1} />
          ))}
        </ul>
      )}
    </li>
  );
}

export default function Trace() {
  const { id = "" } = useParams();
  const [spans, setSpans] = useState<Span[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.trace(id).then(setSpans).catch((e: Error) => setError(e.message));
  }, [id]);
  const { roots, children } = useMemo(() => {
    const children = new Map<string, Span[]>();
    for (const s of spans) if (s.parent_id) children.set(s.parent_id, [...(children.get(s.parent_id) ?? []), s]);
    return { roots: spans.filter((s) => !s.parent_id), children };
  }, [spans]);
  const transitions = roots.flatMap((r) => r.events.filter((e) => e.name === "case.transition"));
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Trace</h1>
      <p className="text-slate-700">
        One trace per case: every agent run, model call and tool call, with timings, tokens and cost. Identifiers are
        keyed hashes and no contents are recorded. <Link className="text-indigo-700 underline" to={`/runs/${id}`}>Back to the case</Link>
      </p>
      {error && <p role="alert">{error}</p>}
      <section aria-labelledby="rules" className="rounded border border-slate-200 bg-white p-4 text-sm">
        <h2 id="rules" className="font-semibold">
          Stage changes and the rules that fired
        </h2>
        <ul>
          {transitions.map((e, i) => (
            <li key={i}>
              {String(e.attributes.from_state)} → {String(e.attributes.to_state)}: {String(e.attributes.status)}
              {e.attributes.rules ? ` (${String(e.attributes.rules)})` : ""}
            </li>
          ))}
        </ul>
      </section>
      <section aria-labelledby="tree" className="rounded border border-slate-200 bg-white p-4 text-sm">
        <h2 id="tree" className="font-semibold">
          Spans ({spans.length})
        </h2>
        <ul className="font-mono text-xs">
          {roots.map((r) => (
            <Node key={r.span_id} span={r} children={children} depth={0} />
          ))}
        </ul>
      </section>
    </div>
  );
}
