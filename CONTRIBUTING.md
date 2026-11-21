# Contributing

- `uv sync`, then `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy` and
  `uv run a15 policy-lint` must pass. For the web app: `cd frontend && npm ci && npm run typecheck && npm run build
  && npm run test:e2e`.
- **A change to prompts, policy or models comes with an eval comparison** (PLAN.md 8.7): a live smoke run
  (`uv run a15 eval run --suite smoke --agent live --record ...`) and `--baseline` against the last report.
  Hard gates must stay at 0.
- **A new safeguard comes with a broken version** that turns its check red (`src/article15/evals/broken.py`).
- **A design decision gets a short record** in `docs/decisions/`.
- **Never commit secrets or real personal data.** Generated data uses reserved `.example` domains, fiction phone
  ranges and documentation IP blocks only.
