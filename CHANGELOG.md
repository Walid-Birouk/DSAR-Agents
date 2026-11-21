# Changelog

All milestones are local and unreleased so far. Versions will follow semantic versioning from the first public
release.

## Unreleased

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
