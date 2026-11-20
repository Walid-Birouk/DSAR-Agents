# Article 15

**A multi-agent desk that answers GDPR right-of-access requests, with evals, observability and policy as code.**

> Status: in development (milestone M0, foundations). A public demo will follow; see [PLAN.md](PLAN.md).

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
```

## Licence

MIT, see [LICENSE](LICENSE). This is a demonstration project, not legal advice.
