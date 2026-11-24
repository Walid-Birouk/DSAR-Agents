# Article 15

[![ci](https://github.com/Walid-Birouk/DSAR-Agents/actions/workflows/ci.yml/badge.svg)](https://github.com/Walid-Birouk/DSAR-Agents/actions/workflows/ci.yml)
![licence: MIT](https://img.shields.io/badge/licence-MIT-blue)

**An LLM agent desk that answers GDPR right-of-access requests, and the code around the model that keeps it
safe, measured on live runs.**

Anyone can ask a company for a copy of all their personal data (GDPR Article 15). The company has one month to
answer, without leaking anyone else's data. Article 15 does that for a fictional French online shop:
1. Read the request.
2. Check who is asking.
3. Search four company systems over MCP.
4. Hide other people's data.
5. Draft and validate the reply.
6. Wait for a person to approve it.

The model is cheap (DeepSeek V4.1 Flash, about USD 0.004 a request) and makes mistakes. A guard layer of
ordinary code decides what it may see, which step comes next and whether a result is acceptable. Every rule
lives once in [`policy.yaml`](src/article15/policy/policy.yaml): it is rendered into the prompts and enforced
by those guards.

## What was measured (live, DeepSeek V4.1 Flash, 78 held-out test requests)

| Question | Result |
|---|---|
| Does the guard layer matter? (E2: guards on against log-only) | with the guards off, **9** requests reached the approval step with a rule broken: 3 leaked another person's details (2 of them injections that worked), 3 skipped a person's decision, 3 harmless; with them on, **0** |
| One agent or seven? (E1) | one agent: **97.4%** fully correct, USD 0.0040 a request; seven agents: 93.6%, USD 0.0050; both 0 hard-gate failures |
| Can policy change safely? (E3: v2 also hides staff names) | 97.4% (v1) and 98.7% (v2), same cost; the redaction guard moved with the policy |
| How stable is it? (3 runs of the 20-case smoke suite) | 95-100% fully correct; 2 of 20 verdicts change between runs |
| Hard gates (impostor accepted, another person's record or data in a reply, release without approval, personal data in telemetry or to the advisor) | **0** on every live run |
| Do the checks themselves fail when they should? | 8 deliberately broken desks: 7 turn the suite red; the eighth (injection defences removed) shows this model resisted all 20 planted injections, so the injection set is too easy ([0018](docs/decisions/0018-provisional-choices-settled.md)) |

Costs come from simulated company systems (SQLite answering in milliseconds), so real systems' latency, integration and
a reviewer's time are not in them. The [eval report](docs/eval-report.md) has every number and every failure.

## What makes it more than a demo

- **Guards in code, measured.** Code refuses whatever breaks a rule:
  - every handoff checked against its schema;
  - stage order fixed by a state machine;
  - identity evidence checked before any record unlocks;
  - discovery must list every record the scoped servers return for the person;
  - redaction verified, including other customers' order numbers, emails and phones;
  - claims in the letter checked against the copy;
  - signed approvals over the exact pack hash.

  E2 measures what slips through without them.
- **Evals with exact ground truth.**
  - **The data:** a seeded fictional company. 160 labelled cases: impostors, namesakes, injections, repeat
    requests, month-end deadlines.
  - **The graders:** exact checks, with an LLM judge only for the letter's tone.
  - **Gates and baselines:** hard gates, bars and baseline comparison.
  - **Testing the tests:** deliberately broken versions, and tampered artifacts, each turn their check red.
- **Record and replay.** Every live model call is recorded; CI replays all 457 recordings for free and fails
  if anything differs. When the code changes, `--rebaseline` and `--continue-from` bring recordings up to date
  without faking a model call.
- **Privacy by design.**
  - Subject-scoped MCP tools: a tricked agent still cannot read someone else's records.
  - No personal data in telemetry (keyed hashes, scrubber, collector redaction; tested at 0).
  - A DPO advisor over A2A that receives only a minimised summary.
  - A refusal letter written by code, so an impostor learns nothing.
- **Operable.**
  - Retries, circuit breakers, provider fallback, checkpoints and resume.
  - OpenTelemetry traces, checked end to end against Jaeger.
  - Daily spend cap, rate limits and a proof-of-work human check.
  - A Docker image, read-only and non-root, smoke-tested in CI; a tag publishes it to GHCR.
  - Prometheus alert rules (spend, system failures, latency, guard-refusal spikes), with a
    [runbook](docs/operations.md).

> **Status (2026-11-23):** built and verified locally; not deployed yet. The frontend can be served from
> Vercel and shows the recorded results on its own; linking it to a backend is described in
> [docs/running.md](docs/running.md). What is still open is listed in
> [decision 0018](docs/decisions/0018-provisional-choices-settled.md), section 4.

## Quick start (development)

```bash
uv sync
uv run pytest
uv run a15 policy-lint            # check the policy against the code guards
uv run a15 policy-render identity # see the policy section of an agent's prompt
uv run a15 deadline 2026-08-31    # GDPR deadline maths (EDPB counting rules)
uv run a15 generate               # build Ferlane's four systems and the 160 test cases (seed 15)
uv run a15 serve crm              # one MCP server over stdio (needs A15_SCOPE_SECRET, see .env.example)
```

Run cases end to end with the single agent (needs `DEEPSEEK_API_KEY` in `.env`; see `.env.example`):

```bash
uv run a15 run A15-004                             # one case, live; writes runs/<time>-deepseek/A15-004/
uv run a15 run --record evals/cassettes/m2         # the five M2 cases, recording every model call
uv run a15 run --replay evals/cassettes/m2         # replay them offline, free; fails if any result differs
```

Each case's run directory holds the case file, the pack and its letter, the transcript, the tool log and the
trace events. Events carry no personal data.

## The web app

The first page shows the guard layer, the code around the model, with numbers from recorded live runs
([decision 0017](docs/decisions/0017-guard-layer-first-page.md)):
- every time code refused the model, by guard, with real examples;
- experiment E2: the same model on the same requests, guards on against guards off;
- what a request costs, and what the simulated systems do not let us measure.

"Follow a request" ([decision 0016](docs/decisions/0016-guided-story.md)) walks through one request. It shows
what a careless answer sends, then replays the recorded run, in which the model slips and a guard catches it.
`uv run a15 eval harness --live <runs> --shadow <run> --guarded <run>` rebuilds the first page's data.

```bash
cd frontend && npm ci && npm run build && cd ..
uv run a15 web                    # http://127.0.0.1:8000; without a model key, prepared cases run scripted
cd frontend && npm run test:e2e   # Playwright: the whole flow in a browser, plus accessibility checks
```

## Advisor and observability

```bash
A15_ADVISOR_KEY=... uv run a15 advisor serve --port 8700     # the DPO advisor as an A2A agent
GRAFANA_ADMIN_PASSWORD=... docker compose -f deploy/observability/docker-compose.yml up -d
A15_OTLP_ENDPOINT=http://127.0.0.1:4318 uv run a15 eval run --suite smoke   # traces to Jaeger (localhost:16686)
```

Every case run also writes `spans.jsonl` (one trace per case, keyed-hash IDs, scrubbed attributes) and, when
the advisor was consulted, `advisor_payloads.jsonl`; the eval's hard gates scan both for every seeded personal
value.

## Evals

Every case is graded against the generator's ground truth with exact checks (PLAN.md 8); only the letter's
quality uses an LLM judge. Reports come as Markdown and JSON, with hard gates, bars and a baseline comparison.

```bash
uv run a15 eval run --suite smoke                    # the oracle agent: free; checks the desk and the graders
uv run a15 eval run --suite smoke --agent live --record evals/cassettes/smoke --budget 1.00
uv run a15 eval run --suite smoke --agent replay --replay evals/cassettes/smoke   # free, exact
uv run a15 eval run --suite smoke --broken redaction_off                         # must come out red
uv run a15 eval run --suite smoke --arm team                                     # the seven-agent desk (E1)
uv run a15 eval run --suite smoke --shadow                                       # guards only log (E2)
uv run a15 eval run --suite test --policy src/article15/policy/policy-v2.yaml      # E3: staff names hidden too
uv run a15 eval run --suite injection --broken injection_defences_off --agent live  # needs a model to fool
uv run a15 eval experiment E1 single/report.json team/report.json                # side by side
# after a code change: bring recordings up to date honestly
uv run a15 eval run --suite smoke --agent replay --replay evals/cassettes/smoke --rebaseline   # free
uv run a15 eval run --cases A15-027 --agent live --record evals/cassettes/golden --continue-from evals/cassettes/golden
uv run a15 eval report runs/eval/<run>/report.json --baseline <baseline>/report.json
uv run a15 eval golden                               # the golden-20 review sheet (evals/golden/)
```

Suites: `smoke` (20 dev cases, every kind), `dev`, `test`, `full` (160), `golden` (20), `injection` (20).
The committed reports of every live run, replayed on the current code, are in [evals/reports/](evals/reports/). The runner is parallel
(`--jobs`), resumable (`--resume`) and budget-capped (`--budget`: a case starts only if its cap still fits).

The fictional shop, its built-in difficulties and how the test cases are labelled are described in
[docs/company.md](docs/company.md). How the servers keep each case to one verified person is in
[decision 0008](docs/decisions/0008-subject-scoped-access.md). How the agent core, the stage tools and
record/replay work is in [decision 0010](docs/decisions/0010-agent-core-and-baseline.md).

## Documentation

- [PLAN.md](PLAN.md): the build plan
- [docs/architecture.md](docs/architecture.md): how the plan maps onto the code
- [docs/decisions/](docs/decisions/README.md): one record per design decision
- [docs/policy.md](docs/policy.md) and [policy.yaml](src/article15/policy/policy.yaml): the rules and their sources
- [docs/eval-report.md](docs/eval-report.md): what is measured so far
- [docs/threat-model.md](docs/threat-model.md): threats and the controls that stop them
- [docs/running.md](docs/running.md): running locally, deploying, self-hosting
- [docs/operations.md](docs/operations.md): the runbook: what each alert means and what to do; safe policy,
  model and code changes

## Licence

MIT, see [LICENSE](LICENSE). This is a demonstration project, not legal advice.
