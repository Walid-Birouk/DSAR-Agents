# Changelog

All milestones are local and unreleased so far. Versions will follow semantic versioning from the first public
release.

## Unreleased

- **First live runs** on DeepSeek V4.1 Flash: the M2 cases, smoke, the golden 20 with the judge, and the test
  split (78 cases, 92.3% fully correct, USD 0.0046 a case), recorded in `evals/cassettes/`. Two bugs they found
  are fixed: the claims check rejected "0 records" for an empty system, and discovery precision counted the
  subject's own records as extra on cases that go to a person. Proof-of-control codes in the simulated desk
  are now keyed (a fixed key for recorded runs, so they replay; a secret per run in the web app). New
  `a15 eval regrade`. The redaction verifier now refuses another customer's order number left readable. The
  golden-20 labels are reviewed (`scripts/golden_check.py`); the review sheet keeps the
  reviewer's `review` block.
- **M7 (in part):** the public demo's protections. An input detector refuses real-looking personal data, live
  runs have per-IP limits and a proof-of-work human check, and visitors can bring their own key. Also a
  Dockerfile and a Compose deployment with Caddy (automatic HTTPS), backups, a load test (20 visitors), the
  architecture, threat model, running guide and eval report docs, and repository hygiene.
- **M6:** the web app. Try it, the run view with the reviewer, the trace view, Break it, Eval results, My runs
  and System, with sandboxes, SSE, an append-only audit log and Playwright tests.
- **M5:** the DPO advisor over A2A with an egress filter, OpenTelemetry without personal data, and resilience:
  retries, circuit breakers, fallback, checkpoints and resume.
- **M4:** the seven-agent desk, proof of control, the claims cross-check, shadow mode for E2, and the release
  service.
- **M3:** the eval harness: suites, exact graders, the LLM judge, a budget-capped runner, reports, and broken
  versions.
- **M2:** the agent core and the single-agent baseline, with record and replay.
- **M1:** the fictional company Ferlane and its MCP servers.
- **M0:** foundations: policy as code, the case state machine and GDPR deadlines.
