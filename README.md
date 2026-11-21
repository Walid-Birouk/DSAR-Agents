# Article 15

**A multi-agent desk that answers GDPR right-of-access requests, with evals, observability and policy as code.**

> Status: built through M7's offline part, not deployed. Every milestone's code and tests are in; no model has
> been called yet (no API key), so the agents' numbers are still to measure. The harness is verified on a perfect
> agent: green on all 160 cases, every hard gate at 0, and seven broken versions that each turn it red
> ([eval report](docs/eval-report.md)). Decisions made while the owner was away are listed in
> [docs/decisions/pending-review.md](docs/decisions/pending-review.md).

Anyone can ask a company for a copy of all their personal data (GDPR Article 15), and the company has one month
to answer without leaking anyone else's data. Article 15 handles that end to end for a fictional French online
shop:

- Seven LLM agents read the request, check identity, search four company systems, redact third parties, and draft
  and validate the reply.
- A person approves the release.
- Every rule lives once in [`policy.yaml`](src/article15/policy/policy.yaml). It is rendered into the agents'
  prompts and enforced by code guards, and a labelled eval suite with hard safety gates checks the result.

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
uv run a15 eval experiment E1 single/report.json team/report.json                # side by side
uv run a15 eval report runs/eval/<run>/report.json --baseline <baseline>/report.json
uv run a15 eval golden                               # the golden-20 review sheet (evals/golden/)
```

Suites: `smoke` (20 dev cases, every kind), `dev`, `test`, `full` (160), `golden` (20). The runner is parallel
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

## Licence

MIT, see [LICENSE](LICENSE). This is a demonstration project, not legal advice.
