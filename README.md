# Article 15

**A multi-agent desk that answers GDPR right-of-access requests, with evals, observability and policy as code.**

> Status: in development (milestone M2: the agent core and a single-agent baseline that runs a case end to end).
> A public demo will follow; see [PLAN.md](PLAN.md).

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

The fictional shop, its built-in difficulties and how the test cases are labelled are described in
[docs/company.md](docs/company.md). How the servers keep each case to one verified person is in
[decision 0008](docs/decisions/0008-subject-scoped-access.md). How the agent core, the stage tools and
record/replay work is in [decision 0010](docs/decisions/0010-agent-core-and-baseline.md).

## Licence

MIT, see [LICENSE](LICENSE). This is a demonstration project, not legal advice.
