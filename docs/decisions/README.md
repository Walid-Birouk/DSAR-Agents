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
