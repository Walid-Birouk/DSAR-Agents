import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, humanCheck, KIND_LABELS, session, type Customer, type Preset, type SessionInfo, type System } from "../api";

export default function TryIt() {
  const navigate = useNavigate();
  const [presets, setPresets] = useState<Preset[]>([]);
  const [system, setSystem] = useState<System | null>(null);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [chosen, setChosen] = useState<string>("");
  const [customer, setCustomer] = useState("");
  const [subject, setSubject] = useState("");
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [me, setMe] = useState<SessionInfo | null>(null);
  const [keyProvider, setKeyProvider] = useState("mistral");
  const [key, setKey] = useState("");

  useEffect(() => {
    api.presets("try").then(setPresets).catch((e: Error) => setError(e.message));
    api.system().then(setSystem).catch(() => undefined);
    api.customers().then(setCustomers).catch(() => undefined);
    session.info().then(setMe).catch(() => undefined);
  }, []);

  const byKind = useMemo(() => {
    const groups = new Map<string, Preset[]>();
    for (const p of presets) groups.set(p.kind, [...(groups.get(p.kind) ?? []), p]);
    return [...groups.entries()];
  }, [presets]);
  const selected = presets.find((p) => p.case_id === chosen);

  async function start(body: Parameters<typeof api.start>[0]) {
    setBusy(true);
    setError(null);
    try {
      const check = me?.live && me.human_check ? await humanCheck() : {};
      const { run_id } = await api.start({ ...body, ...check });
      navigate(`/runs/${run_id}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="space-y-8">
      <section aria-labelledby="intro">
        <h1 id="intro" className="text-2xl font-bold">Try it: be a customer, then be the reviewer</h1>
        <p className="mt-2 max-w-3xl text-slate-700">
          Ferlane is a fictional French online shop. Pick a request one of its customers sent, and watch seven
          agents read it, check who is asking, search four company systems, hide other people's data and draft the
          reply. Then you decide: approve and send, ask for changes, or reject.
        </p>
        {system && (
          <p className="mt-2 text-sm text-slate-600">
            Mode:{" "}
            <strong>
              {me?.own_key ? `live, on your own ${me.own_key} key` : system.mode === "live" ? "live model" : "scripted agent (no model, no cost)"}
            </strong>{" "}
            ·
            policy v{system.policy_version}
          </p>
        )}
      </section>

      {error && (
        <p role="alert" className="rounded border border-red-300 bg-red-50 p-3 text-red-800">
          {error}
        </p>
      )}

      <section aria-labelledby="prepared" className="grid gap-6 md:grid-cols-2">
        <div>
          <h2 id="prepared" className="text-xl font-semibold">
            A prepared request
          </h2>
          <label htmlFor="preset" className="mt-3 block text-sm font-medium">
            Request
          </label>
          <select
            id="preset"
            className="mt-1 w-full rounded border border-slate-300 bg-white p-2"
            value={chosen}
            onChange={(e) => setChosen(e.target.value)}
          >
            <option value="">Choose a request…</option>
            {byKind.map(([kind, items]) => (
              <optgroup key={kind} label={KIND_LABELS[kind] ?? kind}>
                {items.map((p) => (
                  <option key={p.case_id} value={p.case_id}>
                    {p.case_id} · {p.subject} ({p.language})
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
          <button
            type="button"
            disabled={!chosen || busy}
            onClick={() => start({ case_id: chosen })}
            className="mt-3 rounded bg-indigo-700 px-4 py-2 font-medium text-white hover:bg-indigo-800 disabled:bg-slate-400"
          >
            Run this request
          </button>
        </div>
        <div aria-live="polite">
          {selected && (
            <article className="rounded border border-slate-200 bg-white p-4">
              <h3 className="font-semibold">{selected.subject}</h3>
              <p className="text-sm text-slate-600">
                From {selected.sender}, received {selected.received_on}
              </p>
              <pre tabIndex={0} className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap font-sans text-sm">{selected.text}</pre>
            </article>
          )}
        </div>
      </section>

      <section aria-labelledby="own">
        <h2 id="own" className="text-xl font-semibold">
          Or write your own, as one of the customers
        </h2>
        {!me?.live ? (
          <p className="mt-2 text-slate-700">
            Writing your own request needs a live model, which is switched off right now. The prepared requests above
            always work.
          </p>
        ) : (
          <form
            className="mt-3 grid gap-3 md:max-w-2xl"
            onSubmit={(e) => {
              e.preventDefault();
              start({ customer_id: customer, subject, text, source: "visitor" });
            }}
          >
            <label className="text-sm font-medium" htmlFor="customer">
              You are
            </label>
            <select
              id="customer"
              required
              value={customer}
              onChange={(e) => setCustomer(e.target.value)}
              className="rounded border border-slate-300 bg-white p-2"
            >
              <option value="">Choose a fictional customer…</option>
              {customers.map((c) => (
                <option key={c.customer_id} value={c.customer_id}>
                  {c.name} ({c.email})
                </option>
              ))}
            </select>
            <label className="text-sm font-medium" htmlFor="subject">
              Subject
            </label>
            <input
              id="subject"
              maxLength={200}
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              className="rounded border border-slate-300 p-2"
            />
            <label className="text-sm font-medium" htmlFor="text">
              Your request (French or English, no real personal data)
            </label>
            <textarea
              id="text"
              required
              maxLength={4000}
              rows={6}
              value={text}
              onChange={(e) => setText(e.target.value)}
              className="rounded border border-slate-300 p-2"
            />
            <button
              type="submit"
              disabled={busy}
              className="justify-self-start rounded bg-indigo-700 px-4 py-2 font-medium text-white hover:bg-indigo-800 disabled:bg-slate-400"
            >
              Send the request
            </button>
          </form>
        )}
      </section>

      <section aria-labelledby="byok">
        <details>
          <summary id="byok" className="cursor-pointer text-lg font-semibold">
            Bring your own API key
          </summary>
          <p className="mt-2 max-w-3xl text-sm text-slate-700">
            Past the daily budget, or to write your own request, you can use your own Mistral or DeepSeek key. It is
            kept in the server's memory for your session only, for an hour at most, used only for your runs, and never
            written to disk, logs or traces. Your request text goes to that provider: Mistral's EU endpoint keeps it
            in the EU; DeepSeek processes data in China.
          </p>
          {me?.own_key ? (
            <button
              type="button"
              className="mt-2 rounded border border-slate-400 px-3 py-1.5"
              onClick={() => session.dropKey().then(() => session.info().then(setMe))}
            >
              Forget my {me.own_key} key
            </button>
          ) : (
            <form
              className="mt-2 flex flex-wrap items-end gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                session
                  .bringKey(keyProvider, key)
                  .then(() => {
                    setKey("");
                    return session.info().then(setMe);
                  })
                  .catch((err: Error) => setError(err.message));
              }}
            >
              <label className="text-sm">
                Provider
                <select
                  className="ml-1 rounded border border-slate-300 bg-white p-1.5"
                  value={keyProvider}
                  onChange={(e) => setKeyProvider(e.target.value)}
                >
                  <option value="mistral">Mistral (EU)</option>
                  <option value="deepseek">DeepSeek</option>
                </select>
              </label>
              <label className="text-sm">
                Key
                <input
                  type="password"
                  autoComplete="off"
                  className="ml-1 rounded border border-slate-300 p-1.5"
                  value={key}
                  onChange={(e) => setKey(e.target.value)}
                />
              </label>
              <button type="submit" className="rounded bg-slate-800 px-3 py-1.5 text-white">
                Use my key
              </button>
            </form>
          )}
        </details>
      </section>
    </div>
  );
}
