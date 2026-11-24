# Design decisions

Short records of decisions that shape the project. Each states the choice, why it was made, and what was given up.

| # | Decision | Status |
|---|---|---|
| [0001](0001-use-case.md) | Use case: GDPR right-of-access desk | accepted |
| [0002](0002-own-agent-core.md) | Small, framework-free agent core | accepted |
| [0003](0003-model-providers.md) | DeepSeek for development, Mistral EU for the public demo | accepted |
| [0004](0004-public-demo-budget.md) | EUR 2/day demo cap, bring-your-own key beyond it | accepted |
| [0005](0005-policy-as-code.md) | One policy file drives prompts and code guards | accepted |
| [0006](0006-state-machine.md) | Case lifecycle as a state machine in code | accepted |
| [0007](0007-web-and-hosting.md) | React web app, FastAPI, one EU server with Docker Compose | accepted |
| [0008](0008-subject-scoped-access.md) | Subject-scoped access: opaque tokens, identity evidence in code, proof of control | accepted |
| [0009](0009-fictional-company.md) | Ferlane, a seeded fictional company with exact ground truth | accepted |
| [0010](0010-agent-core-and-baseline.md) | Agent core, stage tools, record/replay and the single-agent baseline | accepted |
| [0011](0011-eval-harness.md) | Eval harness: suites, exact graders, judge, budget-capped runner, broken versions | accepted |
| [0012](0012-multi-agent-desk.md) | Seven-agent desk, proof of control, claims cross-check, shadow mode, release service | accepted |
| [0013](0013-advisor-observability-resilience.md) | DPO advisor over A2A, OpenTelemetry without personal data, retries, breakers, checkpoints | accepted |
| [0014](0014-web-app.md) | Web app: FastAPI, sandboxes, SSE, the reviewer, audit log; React app with Playwright tests | accepted |
| [0015](0015-launch-preparation.md) | Launch preparation: input detector, human check, per-IP limits, own keys, Docker/Caddy, load test, docs | accepted |
| [0016](0016-guided-story.md) | The guided story on the first page: one request, the careless answer, the desk replayed, your call | accepted |
| [0017](0017-guard-layer-first-page.md) | The first page shows the guard layer: E2 live, every refusal by guard, honest cost | accepted |
| [0018](0018-provisional-choices-settled.md) | The provisional choices P1-P52 settled; triage, refusal letter, PDF, policy v2; E1, E3 and the eighth broken version live | accepted |
| [0019](0019-judge-review.md) | The letter judge after review: wrapper escaping, notes kept, a stricter agreement rule, exact letter checks | accepted |

The provisional choices made while milestones M2-M7 were built (P1-P52) were settled on 2026-11-23 in
[0018](0018-provisional-choices-settled.md); [provisional-choices.md](provisional-choices.md) keeps the list as proposed.
