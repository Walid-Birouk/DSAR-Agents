# Architecture

The design is in [PLAN.md](../PLAN.md) section 5; the decisions are in [decisions/](decisions/README.md). This
page maps the plan onto the code.

```text
 browser ── HTTPS (Caddy) ──> web app: FastAPI (src/article15/web) + React (frontend/)
                                 | sandboxes per session, job queue, SSE, reviewer, audit log
                                 v
 desk runner (desk/runner.py) ── one case: state machine, scope authority, notifier, checkpoints
   | arm "single": one agent with every tool          | arm "team": coordinator + 6 specialists (desk/team.py)
   v                                                  v
 agent core (core/): bounded loop, tools, caps, record/replay, providers with retries, breakers, fallback
   | MCP (in-process, real protocol)                  | A2A (in-process or a15 advisor serve)
   v                                                  v
 CRM, orders, helpdesk, web logs, notifier            DPO advisor (advisor/): minimised summary, egress filter
 (mcp_servers/, subject-scoped by case tokens)

 every event ──> events.jsonl + OpenTelemetry spans (telemetry/) ──> spans.jsonl, OTLP collector, Jaeger, Grafana
 every run ──> case.json, pack.json, tool_log.jsonl, transcript.json ──> graders (evals/) ──> report.json/md
```

| Plan section | Code |
|---|---|
| 5.2 Agents | `desk/team.py`, `desk/roles/*.md`; single-agent baseline `desk/single_role.md` |
| 5.3 Case lifecycle | `case/machine.py` (transitions and guards), `case/models.py` |
| 5.4 MCP and A2A | `mcp_servers/`, `core/mcp_tools.py`, `scope/`; `advisor/` |
| 5.5 Context design | stage tools and typed handoffs (`desk/case_tools.py`, `schemas.py`); code-built slices (`desk/team.py`) |
| 5.6 Human approval | `desk/release.py`, `case/approvals.py`; the reviewer panel in the web app |
| 6 Policy as code | `policy/policy.yaml`, `policy/render.py`, `policy/lint.py`; guards in `case/machine.py`, `scope/`, `advisor/egress.py` |
| 7 Safety | scoped tools, claims cross-check (`desk/claims.py`), redaction verifier (`desk/redaction.py`), caps |
| 8 Evaluation | `evals/` (suites, graders, metrics, judge, runner, reports, broken versions, experiments, golden 20) |
| 9 Observability | `telemetry/otel.py`, `core/events.py`; `deploy/observability/` |
| 10 Resilience | `core/resilience.py`, `core/live.py`, `desk/checkpoints.py`, operation IDs in `notifier/store.py` |
| 11 Public demo | `web/` (service, guards, store, app), `frontend/`, `deploy/` |
